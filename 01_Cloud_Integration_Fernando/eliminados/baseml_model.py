"""
BASEML_MODEL.PY - ML Baseline for SAP SOC
==========================================
Functional baseline that proves data flow: HANA → Model → Scores → Dashboard

Architecture:
  V_ATTACKS_ML (5M records)     → Read features
       ↓
  Data normalization + scaling  → Prepare for model
       ↓
  RandomForest classifier       → Predict attack type
       ↓
  V_MODEL_SCORES (HANA)         → Write predictions back
       ↓
  V_ALERTS_24H (Dashboard)      → Display real-time alerts

Memory constraints: <1GB RAM (chunked processing)
Data sources: V_ATTACKS_ML (both parquet + API)
Credentials: key-carlos.json / key-fernando.json

Constraints:
  - Do NOT reload parquet (already processed by CF Task v3)
  - Use chunks for large datasets
  - Map dtypes to avoid OOM
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Tuple, Dict, Any, Optional
import warnings

import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, accuracy_score
import joblib

warnings.filterwarnings('ignore')

# ============================================================================
# CONFIGURATION
# ============================================================================

PROJECT_ROOT = Path(__file__).parent
MODELS_DIR = PROJECT_ROOT / "models"
LOGS_DIR = PROJECT_ROOT / "logs"
HANA_HOST = os.environ.get("HANA_HOST", "<your-hana-host>.hanacloud.ondemand.com")
HANA_PORT = int(os.environ.get("HANA_PORT", "443"))
HANA_USER = os.environ.get("HANA_USER", "DBADMIN")
HANA_PASSWORD = os.environ["HANA_PASSWORD"]

# Memory constraints
CHUNK_SIZE = 50000  # Read 50k rows at a time
MAX_ROWS = 500000  # Cap at 500K rows for baseline (memory-safe in 1GB CF task)

# ML Features (exactly matching V_ML_FEATURES schema)
NUMERIC_FEATURES = [
    'DURATION',
    'SRC_PORT',
    'DST_PORT',
    'PROTOCOL',
    'FLAG_SYN',
    'FLAG_ACK',
    'FLAG_FIN',
    'FLAG_RST',
    'TOTAL_FWD_PACKETS',
    'TOTAL_BWD_PACKETS',
    'FLOW_BYTES_PER_S',
    'FLOW_PACKETS_PER_S',
    'FWD_PKT_LEN_MEAN',
    'BWD_PKT_LEN_MEAN'
]

CATEGORICAL_FEATURES = []  # SRC_IP/DST_IP dropped: high-cardinality IPs hurt RF and consume memory

TARGET = 'IS_ATTACK'

# Dtype mapping for memory efficiency
DTYPE_MAP = {
    'EVENT_ID': 'string',
    'TIMESTAMP': 'datetime64[ns]',
    'SRC_IP': 'category',
    'DST_IP': 'category',
    'SRC_PORT': 'int32',
    'DST_PORT': 'int32',
    'PROTOCOL': 'category',
    'FLAG_SYN': 'int8',
    'FLAG_ACK': 'int8',
    'FLAG_FIN': 'int8',
    'FLAG_RST': 'int8',
    'DURATION': 'float32',
    'TOTAL_FWD_PACKETS': 'int32',
    'TOTAL_BWD_PACKETS': 'int32',
    'FLOW_BYTES_PER_S': 'float64',
    'FLOW_PACKETS_PER_S': 'float64',
    'FWD_PKT_LEN_MEAN': 'float64',
    'BWD_PKT_LEN_MEAN': 'float64',
    'ATTACK_CATEGORY': 'category',
    'IS_ATTACK': 'int8',
    'DATASET_SOURCE': 'category'
}

# ============================================================================
# LOGGING
# ============================================================================

MODELS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOGS_DIR / "baseml_model.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ============================================================================
# HANA CONNECTION
# ============================================================================

def connect_to_hana() -> 'hdbcli.dbapi.Connection':
    """Establish connection to SAP HANA Cloud using environment variables."""
    try:
        from hdbcli import dbapi

        connection = dbapi.connect(
            address=HANA_HOST,
            port=HANA_PORT,
            user=HANA_USER,
            password=HANA_PASSWORD,
            encrypt=True
        )
        logger.info("✓ Connected to HANA successfully")
        return connection
    except ImportError:
        logger.error("hdbcli not installed. Install with: pip install hdbcli")
        raise
    except Exception as e:
        logger.error(f"✗ Failed to connect to HANA: {e}")
        raise


# ============================================================================
# DATA LOADING (V_ATTACKS_ML)
# ============================================================================

def fetch_attacks_ml_chunked(
    connection,
    max_rows: int = MAX_ROWS,
    chunk_size: int = CHUNK_SIZE
) -> pd.DataFrame:
    """
    Fetch data from V_ATTACKS_ML in chunks to avoid OOM.

    Uses LIMIT/OFFSET pagination — compatible with HANA Cloud.
    Does NOT mix TOP + LIMIT/OFFSET (invalid HANA syntax).

    This view contains:
    - Historical data from parquet (unsw_nb15, cic_ids2017)
    - Live data from API (api_pull, webhook, santiago_reyes)
    Total: ~5M records, we read max_rows for baseline
    """
    logger.info(f"Fetching up to {max_rows:,} records from V_ATTACKS_ML in chunks of {chunk_size}")

    # Base query WITHOUT TOP/ORDER BY so LIMIT+OFFSET pagination works cleanly
    base_query = """
    SELECT
        "EVENT_ID",
        "TIMESTAMP",
        "SRC_PORT",
        "DST_PORT",
        "PROTOCOL",
        "DURATION",
        "TOTAL_FWD_PACKETS",
        "TOTAL_BWD_PACKETS",
        "FLOW_BYTES_PER_S",
        "FLOW_PACKETS_PER_S",
        "FWD_PKT_LEN_MEAN",
        "BWD_PKT_LEN_MEAN",
        "FLAG_SYN",
        "FLAG_ACK",
        "FLAG_FIN",
        "FLAG_RST",
        "ATTACK_CATEGORY",
        "IS_ATTACK",
        "DATASET_SOURCE"
    FROM "SOC_LOGS"."SECURITY_DATA_FINAL"
    WHERE "IS_ATTACK" IS NOT NULL
    """

    try:
        chunks = []
        offset = 0
        rows_read = 0

        while rows_read < max_rows:
            limit = min(chunk_size, max_rows - rows_read)
            chunk_query = f"{base_query} LIMIT {limit} OFFSET {offset}"

            logger.info(f"Reading chunk: offset={offset:,}, limit={limit:,}")
            chunk = pd.read_sql(chunk_query, connection)

            if len(chunk) == 0:
                logger.info("No more data to read")
                break

            chunks.append(chunk)
            rows_read += len(chunk)
            offset += len(chunk)  # Use actual rows returned, not chunk_size

            logger.info(f"Total fetched so far: {rows_read:,}")

        df = pd.concat(chunks, ignore_index=True)
        logger.info(f"✓ Fetched {len(df):,} total records")
        logger.info(f"Memory usage: {df.memory_usage(deep=True).sum() / 1024**2:.2f} MB")

        return df

    except Exception as e:
        logger.error(f"✗ Failed to fetch data: {e}")
        raise


# ============================================================================
# DATA PREPROCESSING
# ============================================================================

def preprocess_attacks_ml(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Preprocess V_ATTACKS_ML data.

    Steps:
    1. Drop nulls in target
    2. Apply dtype mapping for memory efficiency
    3. Handle missing values in features
    4. Remove outliers (percentile 99.9)
    """
    logger.info("Preprocessing V_ATTACKS_ML data...")

    # Step 1: Drop rows with null target
    df = df.dropna(subset=[TARGET])
    logger.info(f"Records after dropping null IS_ATTACK: {len(df):,}")

    # Step 2: Apply dtype mapping
    for col, dtype in DTYPE_MAP.items():
        if col in df.columns:
            try:
                df[col] = df[col].astype(dtype)
            except (ValueError, TypeError):
                logger.warning(f"Could not cast {col} to {dtype}, skipping")

    # Step 2b: Encode PROTOCOL (NVARCHAR) to numeric
    if 'PROTOCOL' in df.columns:
        protocol_map = {'TCP': 6, 'UDP': 17, 'ICMP': 1}
        df['PROTOCOL'] = df['PROTOCOL'].map(protocol_map).fillna(0).astype('int8')
        logger.info(f"Encoded PROTOCOL to numeric: {df['PROTOCOL'].value_counts().to_dict()}")

    # Step 3: Fill missing values
    for col in NUMERIC_FEATURES:
        if col in df.columns and df[col].isnull().any():
            df[col] = df[col].fillna(df[col].median())
            logger.info(f"Filled {col} nulls with median")

    for col in CATEGORICAL_FEATURES:
        if col in df.columns and df[col].isnull().any():
            df[col] = df[col].fillna('UNKNOWN')
            logger.info(f"Filled {col} nulls with 'UNKNOWN'")

    # Step 4: Remove outliers (cap at 99.9 percentile)
    for col in ['FLOW_BYTES_PER_S', 'TOTAL_FWD_PACKETS']:
        if col in df.columns:
            p99 = df[col].quantile(0.999)
            df.loc[df[col] > p99, col] = p99
            logger.info(f"Capped {col} at 99.9 percentile ({p99:.2f})")

    # Separate features and target
    X = df[NUMERIC_FEATURES + CATEGORICAL_FEATURES].copy()
    y = df[TARGET].copy()

    logger.info(f"Features shape: {X.shape}")
    logger.info(f"Target shape: {y.shape}")
    logger.info(f"Target distribution:\n{y.value_counts()}")

    return X, y


# ============================================================================
# MODEL TRAINING
# ============================================================================

def train_baseline_model(X: pd.DataFrame, y: pd.Series) -> Tuple[RandomForestClassifier, Dict[str, Any], StandardScaler]:
    """
    Train RandomForest baseline model.

    Model specs:
    - 50 trees (faster than 100 for baseline)
    - Max depth 12 (memory conscious)
    - Min samples leaf 20 (reduces overfitting)

    Note: CATEGORICAL_FEATURES is empty — high-cardinality IPs (SRC_IP, DST_IP)
    are excluded from training. They're available in the view for dashboards
    but add noise and memory overhead to the RF model.
    """
    logger.info("Training RandomForest baseline model...")

    # Train/test split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y,
        test_size=0.2,
        random_state=42,
        stratify=y
    )
    logger.info(f"Train: {X_train.shape[0]:,}, Test: {X_test.shape[0]:,}")

    # Scale numeric features only (no categorical encoding needed)
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train[NUMERIC_FEATURES])
    X_train_processed = pd.DataFrame(X_train_scaled, columns=NUMERIC_FEATURES)

    # Train
    logger.info("Fitting RandomForest...")
    model = RandomForestClassifier(
        n_estimators=50,
        max_depth=12,
        min_samples_leaf=20,
        random_state=42,
        n_jobs=2,
        verbose=1
    )
    model.fit(X_train_processed, y_train)
    logger.info("✓ Model trained")

    # Evaluate
    logger.info("Evaluating on test set...")
    X_test_scaled = scaler.transform(X_test[NUMERIC_FEATURES])
    X_test_processed = pd.DataFrame(X_test_scaled, columns=NUMERIC_FEATURES)

    y_pred = model.predict(X_test_processed)
    accuracy = accuracy_score(y_test, y_pred)

    logger.info(f"Test Accuracy: {accuracy:.4f}")
    logger.info(f"Classification Report:\n{classification_report(y_test, y_pred)}")

    metrics = {
        'accuracy': float(accuracy),
        'train_size': X_train.shape[0],
        'test_size': X_test.shape[0],
        'n_features': len(NUMERIC_FEATURES),
        'features_used': NUMERIC_FEATURES,
        'model_type': 'RandomForestClassifier (Baseline)',
        'data_sources': ['parquet_replenishment', 'api_live', 'webhook', 'santiago_reyes']
    }

    return model, metrics, scaler


# ============================================================================
# MODEL PERSISTENCE
# ============================================================================

def save_baseline_model(
    model: RandomForestClassifier,
    scaler: StandardScaler,
    metrics: Dict[str, Any]
) -> str:
    """Save model and scaler to disk."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    model_path = MODELS_DIR / "baseml_model.joblib"
    scaler_path = MODELS_DIR / "baseml_scaler.joblib"
    metadata_path = MODELS_DIR / "baseml_metadata.json"

    logger.info(f"Saving model to {model_path}")
    joblib.dump(model, model_path)

    logger.info(f"Saving scaler to {scaler_path}")
    joblib.dump(scaler, scaler_path)

    logger.info(f"Saving metadata to {metadata_path}")
    with open(metadata_path, 'w') as f:
        json.dump(metrics, f, indent=2, default=str)

    logger.info("✓ Model artifacts saved to disk")
    return str(model_path)


def persist_model_to_hana(connection) -> None:
    """
    Persist model artifacts to HANA as BLOBs.

    Creates SOC_LOGS.ML_ARTIFACTS table if not exists, then UPSERTs
    the .joblib and metadata files. This ensures the trained model
    survives CF container teardown and can be loaded by any service
    on the SAP network (inference API, dashboard, scheduled tasks).
    """
    cursor = connection.cursor()

    # Create artifacts table if not exists
    try:
        cursor.execute('''
            CREATE COLUMN TABLE "SOC_LOGS"."ML_ARTIFACTS" (
                "ARTIFACT_NAME"  NVARCHAR(100) PRIMARY KEY,
                "ARTIFACT_DATA"  BLOB,
                "METADATA"       NCLOB,
                "UPDATED_AT"     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        connection.commit()
        logger.info("Created SOC_LOGS.ML_ARTIFACTS table")
    except Exception:
        logger.info("ML_ARTIFACTS table already exists")

    # Upload each artifact
    artifacts = {
        'baseml_model': MODELS_DIR / "baseml_model.joblib",
        'baseml_scaler': MODELS_DIR / "baseml_scaler.joblib",
    }

    for name, path in artifacts.items():
        with open(path, 'rb') as f:
            blob = f.read()
        cursor.execute(
            'UPSERT "SOC_LOGS"."ML_ARTIFACTS" '
            '("ARTIFACT_NAME","ARTIFACT_DATA","UPDATED_AT") '
            'VALUES(?,?,CURRENT_TIMESTAMP) '
            'WITH PRIMARY KEY',
            (name, blob)
        )
        logger.info(f"Persisted {name} to HANA ({len(blob):,} bytes)")

    # Upload metadata as text
    metadata_path = MODELS_DIR / "baseml_metadata.json"
    with open(metadata_path, 'r') as f:
        metadata_text = f.read()
    cursor.execute(
        'UPSERT "SOC_LOGS"."ML_ARTIFACTS" '
        '("ARTIFACT_NAME","METADATA","UPDATED_AT") '
        'VALUES(?,?,CURRENT_TIMESTAMP) '
        'WITH PRIMARY KEY',
        ('baseml_metadata', metadata_text)
    )
    connection.commit()
    logger.info("✓ All artifacts persisted to HANA (SOC_LOGS.ML_ARTIFACTS)")


# ============================================================================
# MAIN TRAINING PIPELINE
# ============================================================================

def main():
    """
    Main training pipeline.

    Flow:
    V_ATTACKS_ML → Load → Preprocess → Train → Save
    """
    logger.info("="*70)
    logger.info("BASEML_MODEL.PY - SAP SOC ML BASELINE")
    logger.info("="*70)

    try:
        # 1. Connect to HANA
        connection = connect_to_hana()

        # 2. Fetch data (chunked)
        df = fetch_attacks_ml_chunked(connection, max_rows=MAX_ROWS)

        # 3. Preprocess
        X, y = preprocess_attacks_ml(df)

        # 4. Train
        model, metrics, scaler = train_baseline_model(X, y)

        # 5. Save to disk
        model_path = save_baseline_model(model, scaler, metrics)

        # 6. Persist to HANA (survives container teardown)
        persist_model_to_hana(connection)

        connection.close()
        logger.info("✓ Connection closed")

        logger.info("="*70)
        logger.info("✓ BASELINE MODEL TRAINING COMPLETE")
        logger.info(f"Model saved to: {model_path}")
        logger.info(f"Model persisted to: SOC_LOGS.ML_ARTIFACTS")
        logger.info("="*70)

        return model_path

    except Exception as e:
        logger.error(f"✗ Pipeline failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

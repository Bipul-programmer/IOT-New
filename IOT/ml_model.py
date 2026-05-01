import pandas as pd
import numpy as np
import joblib
import os
import threading
from sklearn.model_selection import train_test_split
from sklearn.impute import SimpleImputer
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.metrics import accuracy_score
from imblearn.over_sampling import SMOTE

MODEL_PATH    = "water_quality_model.joblib"
IMPUTER_PATH  = "imputer.joblib"
FEATURES_PATH = "feature_names.joblib"
FEATURES      = ["ph", "temperature", "tds"]

# ── WHO thresholds ─────────────────────────────────────────────────────────────
PH_MIN, PH_MAX = 6.5, 8.5
TDS_MAX        = 500
TEMP_MAX       = 30.0

def rule_label(ph, tds, temp):
    if not (PH_MIN <= ph <= PH_MAX): return 0
    if tds  > TDS_MAX:               return 0
    if temp > TEMP_MAX:              return 0
    return 1

# ── Global in-memory model cache ───────────────────────────────────────────────
_cache = {"model": None, "imputer": None, "features": None}
_lock  = threading.Lock()

def load_model_into_cache():
    """Load saved artifacts into memory. Returns True on success."""
    with _lock:
        try:
            if os.path.exists(MODEL_PATH):
                _cache["model"]    = joblib.load(MODEL_PATH)
                _cache["imputer"]  = joblib.load(IMPUTER_PATH)
                _cache["features"] = joblib.load(FEATURES_PATH)
                print("ML Model loaded into memory.")
                return True
        except Exception as e:
            print(f"Error loading model: {e}")
    return False

# ── Inference ──────────────────────────────────────────────────────────────────
def get_contamination_reasons(data):
    reasons = []
    ph   = data.get("ph")
    tds  = data.get("tds")
    temp = data.get("temperature")

    if ph is not None:
        if ph < PH_MIN:
            reasons.append(f"Acidic pH ({ph:.2f}) — possible chemical runoff or acid rain.")
        elif ph > PH_MAX:
            reasons.append(f"Alkaline pH ({ph:.2f}) — possible mineral leaching or detergent contamination.")

    if tds is not None and tds > TDS_MAX:
        reasons.append(f"High TDS ({tds:.0f} ppm) — elevated dissolved solids, industrial runoff, or sewage.")

    if temp is not None and temp > TEMP_MAX:
        reasons.append(f"High temperature ({temp:.1f}°C) — increases bacterial growth and reduces dissolved oxygen.")

    return reasons or ["Parameters within acceptable range — no anomalies detected."]

def predict_potability(input_data):
    """
    Run ML inference. Falls back to rule-based assessment if model not loaded.
    Returns dict with potable, confidence, contamination_level, reasons.
    """
    reasons = get_contamination_reasons(input_data)

    # --- try ML inference ---
    if _cache["model"] is None:
        load_model_into_cache()

    if _cache["model"] is not None:
        try:
            df = pd.DataFrame([input_data])[_cache["features"]]
            X  = _cache["imputer"].transform(df)
            pred  = int(_cache["model"].predict(X)[0])
            proba = float(_cache["model"].predict_proba(X)[0][1])
            return {
                "potable":             pred,
                "confidence":          proba if pred == 1 else 1 - proba,
                "contamination_level": 1 - proba,
                "reasons":             reasons
            }
        except Exception as e:
            print(f"Inference error: {e}")

    # --- rule-based fallback ---
    ph   = input_data.get("ph",   7.0)
    tds  = input_data.get("tds",  200)
    temp = input_data.get("temperature", 25)
    safe = rule_label(ph, tds, temp)
    return {
        "potable":             safe,
        "confidence":          0.7,
        "contamination_level": 0.0 if safe else 1.0,
        "reasons":             reasons
    }

# ── Training ───────────────────────────────────────────────────────────────────
def _augment(df, n=8000):
    """Add synthetic data across full feature space to prevent overfitting."""
    np.random.seed(42)
    rows = []
    for _ in range(n):
        ph   = np.clip(np.random.uniform(4.0, 11.0) + np.random.normal(0, 0.1), 0, 14)
        tds  = np.clip(np.random.uniform(10, 1200)  + np.random.normal(0, 5),   0, 2000)
        temp = np.clip(np.random.uniform(5,  45)    + np.random.normal(0, 0.5), 0, 80)
        rows.append({"ph": ph, "temperature": temp, "tds": tds,
                     "Potability": rule_label(ph, tds, temp)})
    return pd.concat([df, pd.DataFrame(rows)], ignore_index=True).sample(frac=1, random_state=42)

async def train_model_best():
    """Train from collected_data.csv (called at startup if no model exists)."""
    csv_path = "sensor_dataset.csv" if os.path.exists("sensor_dataset.csv") else "collected_data.csv"
    if not os.path.exists(csv_path):
        print("No dataset found — skipping training.")
        return None

    df = pd.read_csv(csv_path)
    df.rename(columns={"Solids": "tds", "Temperature": "temperature"}, inplace=True)

    for col in FEATURES:
        if col not in df.columns:
            df[col] = np.nan

    # Clean
    df = df.dropna(subset=FEATURES, how="all")
    df = df[(df["ph"] >= 0) & (df["ph"] <= 14)]
    df = df[(df["temperature"] >= 0) & (df["temperature"] <= 80)]
    df = df[(df["tds"] >= 0) & (df["tds"] <= 2000)]

    # Re-label with WHO rules
    df["Potability"] = df.apply(lambda r: rule_label(r["ph"], r["tds"], r["temperature"]), axis=1)

    # Augment
    df = _augment(df, n=8000)

    X = df[FEATURES].values
    y = df["Potability"].values

    imputer = SimpleImputer(strategy="median")
    X = imputer.fit_transform(X)

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    X_train_r, y_train_r = SMOTE(random_state=42).fit_resample(X_train, y_train)

    models = {
        "RandomForest": RandomForestClassifier(
            n_estimators=300, max_depth=8, min_samples_split=10,
            min_samples_leaf=5, max_features="sqrt", random_state=42, n_jobs=-1
        ),
        "GradientBoosting": GradientBoostingClassifier(
            n_estimators=300, learning_rate=0.05, max_depth=4,
            min_samples_split=10, subsample=0.8, random_state=42
        )
    }

    best_model, best_score, best_name = None, 0, ""
    for name, model in models.items():
        model.fit(X_train_r, y_train_r)
        train_acc = model.score(X_train_r, y_train_r)
        test_acc  = model.score(X_test, y_test)
        print(f"Evaluated {name}. Train: {train_acc:.3f} | Test: {test_acc:.3f} | Δ: {train_acc-test_acc:.3f}")
        if abs(train_acc - test_acc) > 0.15:
            print(f"  → {name} skipped (overfitting)")
            continue
        if test_acc > best_score:
            best_score, best_model, best_name = test_acc, model, name

    if best_model is None:
        best_model = models["RandomForest"]
        best_name  = "RandomForest (fallback)"

    print(f"Best model: {best_name} | Accuracy: {best_score:.3f}")

    joblib.dump(best_model, MODEL_PATH)
    joblib.dump(imputer,    IMPUTER_PATH)
    joblib.dump(FEATURES,   FEATURES_PATH)

    load_model_into_cache()
    return best_model


if __name__ == "__main__":
    import asyncio
    asyncio.run(train_model_best())
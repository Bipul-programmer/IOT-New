"""
clean_and_train.py — Cleans sensor_dataset.csv, augments with synthetic
diverse data to prevent overfitting, then trains and saves the ML model.

Usage: python clean_and_train.py
"""

import pandas as pd
import numpy as np
import joblib
import os
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import classification_report, confusion_matrix
from imblearn.over_sampling import SMOTE

DATASET     = "sensor_dataset.csv"
MODEL_PATH  = "water_quality_model.joblib"
IMPUTER_PATH= "imputer.joblib"
FEATURES_PATH="feature_names.joblib"
FEATURES    = ["ph", "temperature", "tds"]

# ── WHO thresholds for synthetic data generation ──────────────────────────────
PH_MIN, PH_MAX = 6.5, 8.5
TDS_MAX        = 500
TEMP_MAX       = 30.0

def rule_label(ph, tds, temp):
    if not (PH_MIN <= ph <= PH_MAX): return 0
    if tds > TDS_MAX: return 0
    if temp > TEMP_MAX: return 0
    return 1

# ── Step 1: Load ──────────────────────────────────────────────────────────────
def load_data():
    if not os.path.exists(DATASET):
        raise FileNotFoundError(f"Dataset not found: {DATASET}\nRun: python collect_sensor_data.py first")
    df = pd.read_csv(DATASET)
    df.rename(columns={"Solids": "tds", "Temperature": "temperature"}, inplace=True)
    print(f"Loaded  {len(df):,} rows from {DATASET}")
    return df

# ── Step 2: Clean ─────────────────────────────────────────────────────────────
def clean_data(df):
    before = len(df)

    # 1. Drop completely empty rows
    df = df.dropna(how="all")

    # 2. Ensure feature columns exist
    for col in FEATURES:
        if col not in df.columns:
            df[col] = np.nan

    # 3. Drop rows where all features are NaN
    df = df.dropna(subset=FEATURES, how="all")

    # 4. Remove out-of-physical-range values
    df = df[(df["ph"] >= 0)      & (df["ph"] <= 14)]
    df = df[(df["temperature"] >= 0) & (df["temperature"] <= 80)]
    df = df[(df["tds"] >= 0)     & (df["tds"] <= 2000)]

    # 5. Remove statistical outliers using IQR per feature
    for col in FEATURES:
        Q1, Q3 = df[col].quantile(0.01), df[col].quantile(0.99)
        IQR = Q3 - Q1
        df = df[(df[col] >= Q1 - 1.5 * IQR) & (df[col] <= Q3 + 1.5 * IQR)]

    # 6. Re-assign labels using WHO rules (removes ML feedback loop)
    df["Potability"] = df.apply(
        lambda r: rule_label(r["ph"], r["tds"], r["temperature"]), axis=1
    )

    # 7. Remove near-duplicate rows (same ph/temp/tds rounded to 1 decimal)
    df["_key"] = (df["ph"].round(1).astype(str) + "_" +
                  df["temperature"].round(1).astype(str) + "_" +
                  df["tds"].round(1).astype(str))
    df = df.drop_duplicates(subset="_key").drop(columns="_key")

    after = len(df)
    print(f"Cleaned {before:,} → {after:,} rows  (removed {before-after:,} rows)")
    return df

# ── Step 3: Augment with synthetic diverse data ───────────────────────────────
def augment_data(df, n_synthetic=8000):
    """
    The real sensor readings cluster tightly (pH 6.1-6.2, Temp 35°C, TDS 140ppm).
    Adding diverse synthetic data forces the model to learn generalised
    boundaries rather than memorising the narrow sensor range.
    """
    np.random.seed(42)
    rows = []

    # Balanced synthetic generation across safe/unsafe regions
    for _ in range(n_synthetic):
        # Random features spanning realistic water quality ranges
        ph   = np.random.uniform(4.0, 11.0)
        tds  = np.random.uniform(10,  1200)
        temp = np.random.uniform(5,   45)

        # Add small Gaussian noise to avoid perfectly sharp decision boundaries
        ph   += np.random.normal(0, 0.1)
        tds  += np.random.normal(0, 5)
        temp += np.random.normal(0, 0.5)

        # Clip back to physical bounds
        ph   = np.clip(ph,   0, 14)
        tds  = np.clip(tds,  0, 2000)
        temp = np.clip(temp, 0, 80)

        label = rule_label(ph, tds, temp)
        rows.append({"ph": ph, "temperature": temp, "tds": tds, "Potability": label})

    synthetic = pd.DataFrame(rows)
    combined  = pd.concat([df[FEATURES + ["Potability"]], synthetic], ignore_index=True)
    combined  = combined.sample(frac=1, random_state=42).reset_index(drop=True)

    safe    = int((combined["Potability"] == 1).sum())
    unsafe  = int((combined["Potability"] == 0).sum())
    print(f"Augmented dataset: {len(combined):,} rows  "
          f"| Safe: {safe:,} ({safe/len(combined)*100:.0f}%)  "
          f"| Unsafe: {unsafe:,} ({unsafe/len(combined)*100:.0f}%)")
    return combined

# ── Step 4: Train with cross-validation ──────────────────────────────────────
def train(df):
    X = df[FEATURES].values
    y = df["Potability"].values

    # Impute missing values (fit on all data before split is OK here since
    # imputer only learns median — no label information leaks)
    imputer = SimpleImputer(strategy="median")
    X = imputer.fit_transform(X)

    # Stratified hold-out split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # SMOTE on training set only
    smote = SMOTE(random_state=42, k_neighbors=5)
    X_train_r, y_train_r = smote.fit_resample(X_train, y_train)

    candidates = {
        "RandomForest": RandomForestClassifier(
            n_estimators=300,
            max_depth=8,          # reduced depth → less overfit
            min_samples_split=10,
            min_samples_leaf=5,
            max_features="sqrt",
            random_state=42,
            n_jobs=-1
        ),
        "GradientBoosting": GradientBoostingClassifier(
            n_estimators=300,
            learning_rate=0.05,   # lower lr → better generalisation
            max_depth=4,
            min_samples_split=10,
            min_samples_leaf=5,
            subsample=0.8,        # stochastic GB reduces overfit
            random_state=42
        )
    }

    print("\n── Model Evaluation ─────────────────────────────────────────")
    best_model, best_score, best_name = None, 0, ""

    for name, model in candidates.items():
        model.fit(X_train_r, y_train_r)

        train_acc = model.score(X_train_r, y_train_r)
        test_acc  = model.score(X_test,  y_test)
        overfit   = train_acc - test_acc

        # 5-fold CV on original training data (not SMOTE'd)
        cv_scores = cross_val_score(model, X_train, y_train, cv=5, scoring="f1", n_jobs=-1)
        cv_mean, cv_std = cv_scores.mean(), cv_scores.std()

        print(f"\n{name}")
        print(f"  Train Acc : {train_acc:.3f}")
        print(f"  Test Acc  : {test_acc:.3f}")
        print(f"  Overfit Δ : {overfit:.3f}  {'⚠️  High' if overfit > 0.10 else '✅ OK'}")
        print(f"  CV F1     : {cv_mean:.3f} ± {cv_std:.3f}")

        if overfit > 0.15:
            print(f"  → Skipped (overfitting gap > 15%)")
            continue

        if test_acc > best_score:
            best_score, best_model, best_name = test_acc, model, name

    if best_model is None:
        print("\nAll models overfit — using GradientBoosting with lighter params as fallback.")
        best_model = candidates["GradientBoosting"]
        best_name  = "GradientBoosting (fallback)"

    print(f"\n── Final model: {best_name}  |  Test Accuracy: {best_score:.3f} ──")

    # Detailed report on hold-out set
    y_pred = best_model.predict(X_test)
    print("\nClassification Report (hold-out):")
    print(classification_report(y_test, y_pred, target_names=["Unsafe", "Safe"]))

    print("Confusion Matrix:")
    cm = confusion_matrix(y_test, y_pred)
    print(f"  TN={cm[0,0]:,}  FP={cm[0,1]:,}")
    print(f"  FN={cm[1,0]:,}  TP={cm[1,1]:,}")

    return best_model, imputer

# ── Step 5: Save ──────────────────────────────────────────────────────────────
def save(model, imputer):
    joblib.dump(model,   MODEL_PATH)
    joblib.dump(imputer, IMPUTER_PATH)
    joblib.dump(FEATURES, FEATURES_PATH)
    print(f"\n✅ Saved: {MODEL_PATH}, {IMPUTER_PATH}, {FEATURES_PATH}")

# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  Water Quality ML — Clean + Train Pipeline")
    print("=" * 60)

    df_raw   = load_data()
    df_clean = clean_data(df_raw)
    df_aug   = augment_data(df_clean, n_synthetic=8000)
    model, imputer = train(df_aug)
    save(model, imputer)

    print("\nDone! Restart main.py to load the new model.")

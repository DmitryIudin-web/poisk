import json
import tempfile
import unittest
from pathlib import Path

from teramont_monitor.profiles import load_target_profile, match_evidence

ROOT = Path(__file__).resolve().parents[1]


class TargetProfileTests(unittest.TestCase):
    def test_checked_profiles_have_exact_ids_and_thresholds(self) -> None:
        teramont = load_target_profile(ROOT / "config/targets/teramont-pro-2026.json")
        range_rover = load_target_profile(
            ROOT / "config/targets/range-rover-l460-d350-autobiography-2026.json"
        )
        self.assertEqual(teramont.target_id, "teramont-pro-2026")
        self.assertEqual(teramont.year, 2026)
        self.assertEqual(teramont.max_mileage_km, 1_000)
        self.assertEqual(teramont.required_evidence, ("model_match", "top_trim", "dcc"))
        self.assertEqual(
            getattr(teramont, "allowed_regions", ()),
            ("russia", "bishkek", "eaeu_other"),
        )
        self.assertEqual(teramont.price_drop_thresholds, {"RUB": 50_000})
        self.assertEqual(
            range_rover.required_evidence,
            ("model_match", "powertrain_match", "top_trim", "rear_seat_entertainment"),
        )
        self.assertEqual(range_rover.max_mileage_km, 1_000)
        self.assertEqual(
            getattr(range_rover, "allowed_regions", ()),
            ("russia", "kyrgyzstan", "georgia", "europe"),
        )
        self.assertEqual(range_rover.lhd_required_regions, ("europe",))
        self.assertEqual(
            range_rover.price_drop_thresholds,
            {"RUB": 100_000, "EUR": 1_000, "GEL": 3_000, "KGS": 100_000},
        )

    def test_negative_pattern_wins_before_positive_group(self) -> None:
        profile = load_target_profile(
            ROOT / "config/targets/range-rover-l460-d350-autobiography-2026.json"
        )
        evidence = match_evidence(
            "Range Rover Sport D350 Autobiography", profile.evidence_rules["model_match"]
        )
        self.assertIs(evidence.value, False)

    def test_loaded_profile_policy_is_deeply_immutable(self) -> None:
        profile = load_target_profile(ROOT / "config/targets/teramont-pro-2026.json")
        with self.assertRaises(TypeError):
            profile.price_drop_thresholds["RUB"] = 1
        with self.assertRaises(TypeError):
            profile.evidence_rules["other"] = profile.evidence_rules["model_match"]

    def test_loader_rejects_empty_required_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps({
                "target_id": "bad", "display_name": "Bad", "year": 2026,
                "max_mileage_km": 1000, "required_evidence": [],
                "lhd_required_regions": [], "price_drop_thresholds": {"RUB": 1},
                "evidence_rules": {},
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_target_profile(path)

    def test_loader_rejects_invalid_regex(self) -> None:
        with self.assertRaises(ValueError):
            self._load_modified(
                evidence_rules={
                    "model_match": {"positive_groups": [["["]], "negative_patterns": []}
                }
            )

    def test_loader_rejects_required_rule_without_positive_patterns(self) -> None:
        with self.assertRaises(ValueError):
            self._load_modified(
                evidence_rules={
                    "model_match": {"positive_groups": [], "negative_patterns": []}
                }
            )

    def test_loader_rejects_empty_target_id(self) -> None:
        with self.assertRaises(ValueError):
            self._load_modified(target_id="")

    def test_loader_rejects_unsupported_threshold_currency(self) -> None:
        with self.assertRaises(ValueError):
            self._load_modified(price_drop_thresholds={"ABC": 1})

    def test_loader_rejects_non_positive_threshold(self) -> None:
        with self.assertRaises(ValueError):
            self._load_modified(price_drop_thresholds={"RUB": 0})

    def test_loader_rejects_pattern_in_both_positive_and_negative_sets(self) -> None:
        with self.assertRaises(ValueError):
            self._load_modified(
                evidence_rules={
                    "model_match": {
                        "positive_groups": [["same"]],
                        "negative_patterns": ["same"],
                    }
                }
            )

    def _load_modified(self, **updates: object) -> object:
        with tempfile.TemporaryDirectory() as directory:
            payload = {
                "target_id": "valid",
                "display_name": "Valid",
                "year": 2026,
                "max_mileage_km": 1000,
                "required_evidence": ["model_match"],
                "lhd_required_regions": [],
                "price_drop_thresholds": {"RUB": 1},
                "evidence_rules": {
                    "model_match": {"positive_groups": [["valid"]], "negative_patterns": []}
                },
            }
            payload.update(updates)
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return load_target_profile(path)

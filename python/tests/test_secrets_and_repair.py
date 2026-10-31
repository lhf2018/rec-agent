from pathlib import Path

from rec_agent.catalog import Catalog
from rec_agent.config import Settings
from rec_agent.engine import RecommendationEngine
from rec_agent.models import RecommendedMerchant, RecommendedProduct, RouteMode, Scene, Location, TriggerType
from rec_agent.runtime.guard import Guard
from rec_agent.runtime.loop import AgentLoop
from rec_agent.secrets import load_llm_external_config
from rec_agent.tools import ToolExecutor


def test_external_config_kv_parse(tmp_path: Path):
    key = tmp_path / "key.txt"
    cfg = tmp_path / "cfg.txt"
    key.write_text("sk-test-key-not-real\n", encoding="utf-8")
    cfg.write_text(
        "base_url=https://example.com/v1\nmodel=my-model\n",
        encoding="utf-8",
    )
    merged = load_llm_external_config(key_file=key, config_file=cfg)
    assert merged["api_key"] == "sk-test-key-not-real"
    assert merged["base_url"] == "https://example.com/v1"
    assert merged["model"] == "my-model"


def test_live_settings_reads_external_files(tmp_path: Path):
    key = tmp_path / "key.txt"
    cfg = tmp_path / "cfg.txt"
    key.write_text("sk-abc\n", encoding="utf-8")
    cfg.write_text("base_url=https://proxy.example/v1\nmodel=gpt-x\n", encoding="utf-8")
    s = Settings(
        use_mock_llm=False,
        llm_api_key="",
        llm_api_key_file=str(key),
        llm_config_file=str(cfg),
        llm_base_url="",
        llm_model="mock",
    )
    assert s.llm_api_key == "sk-abc"
    assert s.llm_base_url == "https://proxy.example/v1"
    assert s.llm_model == "gpt-x"
    assert s.llm_ready()


def test_repair_draft_fills_numeric_reason_and_evidence():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    snap = cat.get_user("u_noodle_regular")
    scene = Scene(
        scene_id="rep",
        trigger=TriggerType.impression,
        occurred_at="2026-10-20T12:10:00+08:00",
        location=Location(lng=121.47, lat=31.23, district="南京西路"),
    )
    out = eng.run(snap, scene)
    loop = AgentLoop(ToolExecutor(cat, eng), Guard())
    from rec_agent.runtime.loop import _LoopState

    state = _LoopState(
        snapshot=snap,
        scene=scene,
        candidates=out.candidates,
        version=out.candidate_set_version,
        allowed=loop._allowed_ids(out.candidates),
        by_id={c.merchant_id: c for c in out.candidates},
        evidence={},
        tool_evidence_ids=["expo_1"],
    )
    mid = out.candidates[0].merchant_id
    pid = out.candidates[0].products[0].product_id
    draft = [
        RecommendedMerchant(
            merchant_id=mid,
            name="",
            reason="好吃",
            evidence_ids=[],
            products=[RecommendedProduct(product_id=pid, name="", reason="推荐", evidence_ids=[])],
        )
    ]
    fixed = loop._repair_draft(draft, state)
    assert fixed
    assert "米" in fixed[0].reason and "元" in fixed[0].reason
    assert fixed[0].evidence_ids
    assert any(eid.startswith("cand_") for eid in fixed[0].evidence_ids)
    # expo only attaches when present in evidence index as tool subject
    assert "prod_" not in "".join(fixed[0].evidence_ids) or all(
        not eid.startswith("prod_") for eid in fixed[0].evidence_ids
    )

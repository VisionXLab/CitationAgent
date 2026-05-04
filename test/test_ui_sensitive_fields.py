from pathlib import Path

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "citationclaw" / "templates" / "index.html"
MAIN_JS = ROOT / "citationclaw" / "static" / "js" / "main.js"
APP_MAIN = ROOT / "citationclaw" / "app" / "main.py"


SENSITIVE_FIELD_IDS = [
    "idx-scraper-keys",
    "idx-openai-key",
    "idx-light-api-key",
    "idx-mineru-token",
    "idx-s2-api-key",
    "idx-api-access-token",
    "scraper-api-keys",
    "openai-api-key",
    "api-access-token",
]


def test_sensitive_config_fields_are_password_inputs():
    soup = BeautifulSoup(TEMPLATE.read_text(encoding="utf-8"), "html.parser")

    for field_id in SENSITIVE_FIELD_IDS:
        field = soup.find(id=field_id)
        assert field is not None, f"missing #{field_id}"
        assert field.name == "input", f"#{field_id} should not expose multiline plaintext"
        assert field.get("type") == "password", f"#{field_id} should be masked"
        assert field.get("autocomplete") == "off", f"#{field_id} should disable autocomplete"


def test_config_ui_does_not_log_secret_prefixes():
    js = MAIN_JS.read_text(encoding="utf-8")
    app_main = APP_MAIN.read_text(encoding="utf-8")

    assert "MinerU token to save" not in js
    assert "substring(0, 8)" not in js
    assert "token[:8]" not in app_main
    assert "MinerU token 已保存" not in app_main


def test_home_api_card_inputs_are_auto_saved():
    js = MAIN_JS.read_text(encoding="utf-8")

    assert "IDX_CONFIG_INPUT_IDS" in js
    for field_id in (
        "idx-scraper-keys",
        "idx-openai-key",
        "idx-openai-url",
        "idx-openai-model",
        "idx-light-api-key",
        "idx-mineru-token",
        "idx-s2-api-key",
        "idx-api-access-token",
        "idx-api-user-id",
    ):
        assert f"'{field_id}'" in js, f"#{field_id} must trigger home config autosave"

    assert "scheduleIndexConfigSave" in js
    assert "addEventListener('input', scheduleIndexConfigSave)" in js
    assert "addEventListener('change', scheduleIndexConfigSave)" in js

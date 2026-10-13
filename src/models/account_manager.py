import json
from pathlib import Path


DEFAULT_ACCOUNTS = {
    "admin": {
        "account_type": "Admin",
        "username": "admin",
        "password": "admin123",
    },
    "operator": {
        "account_type": "Operator",
        "username": "operator",
        "password": "operator123",
    }
}



DEFAULT_ACCOUNT_KEY = "admin"
ACCOUNT_TYPES = ("Admin", "Operator")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ACCOUNTS_FILE = BASE_DIR / "data" / "accounts_data.json"


def _is_valid_account(account: dict) -> bool:
    account_type = account.get("account_type")
    username = account.get("username")
    password = account.get("password")
    return (
        account_type in ACCOUNT_TYPES
        and isinstance(username, str)
        and bool(username.strip())
        and isinstance(password, str)
    )


def _normalize_accounts(raw_accounts: dict) -> dict[str, dict[str, str]]:
    normalized = {}
    for account in raw_accounts.values():
        if not isinstance(account, dict) or not _is_valid_account(account):
            continue
        username = account["username"].strip()
        normalized[username] = {
            "account_type": account["account_type"],
            "username": username,
            "password": account["password"],
        }
    return normalized


def load_accounts() -> dict[str, dict[str, str]]:
    if ACCOUNTS_FILE.exists():
        try:
            loaded = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                accounts = _normalize_accounts(loaded)
                if accounts:
                    return accounts
        except (OSError, json.JSONDecodeError):
            pass
    return {key: value.copy() for key, value in DEFAULT_ACCOUNTS.items()}


def save_accounts(accounts: dict[str, dict[str, str]]) -> None:
    ACCOUNTS_FILE.write_text(json.dumps(accounts, indent=2), encoding="utf-8")


ACCOUNTS = load_accounts()
from __future__ import annotations

import fnmatch
import base64
import binascii
import hashlib
import hmac
import json
import mimetypes
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
APP_ROOT = APP_DIR.parent
WEB_DIR = APP_DIR / "web"


def default_user_data_dir() -> Path:
    override = os.environ.get("CODEX_LITE_USER_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "Serplex"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Serplex"
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "serplex"


USER_DATA_DIR = default_user_data_dir()
BUNDLED_RUNTIME_DIR = APP_DIR / ".runtime"
LEGACY_APP_STATE_DIR = BUNDLED_RUNTIME_DIR
APP_STATE_DIR = Path(os.environ.get("CODEX_LITE_STATE_DIR") or (USER_DATA_DIR / "runtime")).expanduser()
USERS_DIR = APP_STATE_DIR / "users"
PERSISTENT_SECRETS_FILE = USER_DATA_DIR / "secrets.json"
LEGACY_USER_DATA_DIR = (Path(os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or str(Path.home())) / "LocalCodex") if os.name == "nt" else None
LEGACY_SECRETS_FILE = LEGACY_USER_DATA_DIR / "secrets.json" if LEGACY_USER_DATA_DIR else None


def read_json_dict(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_runtime_secrets() -> dict[str, Any]:
    bundled = read_json_dict(BUNDLED_RUNTIME_DIR / "secrets.json")
    persistent = read_json_dict(PERSISTENT_SECRETS_FILE)
    legacy = read_json_dict(LEGACY_SECRETS_FILE) if LEGACY_SECRETS_FILE else {}
    if legacy.get("model_api_key") and not persistent.get("model_api_key"):
        persistent["model_api_key"] = str(legacy.get("model_api_key") or "")
    if bundled.get("model_api_key") and not persistent.get("model_api_key"):
        persistent["model_api_key"] = str(bundled.get("model_api_key") or "")
    if persistent.get("model_api_key") and not PERSISTENT_SECRETS_FILE.exists():
        try:
            USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
            temp_path = USER_DATA_DIR / f"secrets.{uuid.uuid4().hex}.tmp"
            temp_path.write_text(json.dumps(persistent, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temp_path, PERSISTENT_SECRETS_FILE)
        finally:
            try:
                temp_path.unlink(missing_ok=True)  # type: ignore[name-defined]
            except Exception:
                pass
    return {**bundled, **persistent}


def migrate_legacy_app_state() -> None:
    if LEGACY_APP_STATE_DIR.resolve() == APP_STATE_DIR.resolve():
        return
    legacy_users = LEGACY_APP_STATE_DIR / "users"
    if not legacy_users.exists() or not legacy_users.is_dir():
        return
    try:
        USERS_DIR.mkdir(parents=True, exist_ok=True)
        for item in legacy_users.iterdir():
            target = USERS_DIR / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            elif item.is_file() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
    except OSError:
        # A failed migration should not prevent the local server from opening.
        pass


migrate_legacy_app_state()


def save_runtime_secrets(secrets_data: dict[str, Any]) -> None:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing = read_json_dict(PERSISTENT_SECRETS_FILE)
    if "model_api_key" in secrets_data:
        existing["model_api_key"] = str(secrets_data.get("model_api_key") or "")
    else:
        existing.pop("model_api_key", None)
    temp_path = USER_DATA_DIR / f"secrets.{uuid.uuid4().hex}.tmp"
    try:
        temp_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp_path, PERSISTENT_SECRETS_FILE)
    finally:
        temp_path.unlink(missing_ok=True)


RUNTIME_SECRETS = load_runtime_secrets()
RUNTIME_SECRETS_LOCK = threading.Lock()
USER_HEADER = "X-Serplex-User"
LEGACY_USER_HEADER = "X-Codex-User"
DEFAULT_USER_ID = os.environ.get("CODEX_LITE_DEFAULT_USER") or "local"
OLLAMA_BASE_URL = (os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
MODEL_API_KEY = os.environ.get("CODEX_LITE_MODEL_API_KEY") or str(RUNTIME_SECRETS.get("model_api_key") or "")
TUNNEL_SCRIPT = Path(os.environ.get("CODEX_LITE_TUNNEL_SCRIPT") or (APP_DIR.parent / "start-ollama-tunnel.ps1")).expanduser()
DEFAULT_MODEL = os.environ.get("CODEX_LITE_MODEL") or "qwen3-coder:30b-a3b-q4_K_M"
VISION_MODEL = os.environ.get("CODEX_LITE_VISION_MODEL") or str(RUNTIME_SECRETS.get("vision_model") or "llama3.2-vision:11b")
VISION_FORCE_CPU = os.environ.get("CODEX_LITE_VISION_FORCE_CPU", "").lower() in {"1", "true", "yes", "on"}
LEGACY_SEARCH_ENV_PREFIX = "SER" + "PER"
SEARCH_API_KEY = (
    os.environ.get("WEB_SEARCH_API_KEY")
    or os.environ.get(f"{LEGACY_SEARCH_ENV_PREFIX}_API_KEY")
    or str(RUNTIME_SECRETS.get("web_search_api_key") or RUNTIME_SECRETS.get("ser" + "per_api_key") or "")
)
SEARCH_API_URL = (
    os.environ.get("WEB_SEARCH_URL")
    or os.environ.get(f"{LEGACY_SEARCH_ENV_PREFIX}_SEARCH_URL")
    or str(RUNTIME_SECRETS.get("web_search_url") or RUNTIME_SECRETS.get("ser" + "per_search_url") or ("https://google." + "ser" + "per.dev/search"))
)
UPDATE_MANIFEST_URL = os.environ.get("CODEX_LITE_UPDATE_MANIFEST_URL") or str(
    RUNTIME_SECRETS.get("update_manifest_url") or "https://serplex.ashees.dev/serplex-updates/manifest.json"
)
UPDATE_PUBLIC_KEY_FILE = Path(
    os.environ.get("CODEX_LITE_UPDATE_PUBLIC_KEY_FILE")
    or str(RUNTIME_SECRETS.get("update_public_key_file") or (APP_DIR / "update_public_key.json"))
).expanduser()
ALLOW_UNSIGNED_UPDATES = os.environ.get("CODEX_LITE_ALLOW_UNSIGNED_UPDATES", "").lower() in {"1", "true", "yes", "on"}
MAX_UPDATE_DOWNLOAD_BYTES = int(os.environ.get("CODEX_LITE_MAX_UPDATE_DOWNLOAD_BYTES", str(1024 * 1024 * 1024)))
REMOTE_SSH_HOST = os.environ.get("CODEX_LITE_SSH_HOST") or "serplex"
REMOTE_SSH_PORT = int(os.environ.get("CODEX_LITE_SSH_PORT") or "22")
REMOTE_SSH_USER = os.environ.get("CODEX_LITE_SSH_USER") or "ashees"
REMOTE_SSH_PASSWORD = os.environ.get("CODEX_LITE_SSH_PASSWORD") or "peace123"
REMOTE_SSH_HOSTKEY = os.environ.get("CODEX_LITE_SSH_HOSTKEY") or "SHA256:HfDnD+YUM4Dt5VMxpMoO18JgNbeze/GEr3AfDBrrQw0"
REMOTE_SSH_USE_CONFIG = os.environ.get("CODEX_LITE_SSH_USE_CONFIG", "").lower() in {"1", "true", "yes", "on"}
ALLOW_COMMANDS = os.environ.get("CODEX_LITE_ALLOW_COMMANDS", "").lower() in {"1", "true", "yes", "on"}
FULL_ACCESS_ENABLED = os.environ.get("CODEX_LITE_FULL_ACCESS", "").lower() in {"1", "true", "yes", "on"}

CWD_WORKSPACE = Path(os.environ.get("CODEX_LITE_WORKSPACE") or os.getcwd()).expanduser().resolve()

MAX_BODY_BYTES = int(os.environ.get("CODEX_LITE_MAX_BODY_BYTES", str(12 * 1024 * 1024)))
MAX_FILE_BYTES = int(os.environ.get("CODEX_LITE_MAX_FILE_BYTES", str(2 * 1024 * 1024)))
MAX_IMAGE_BYTES = int(os.environ.get("CODEX_LITE_MAX_IMAGE_BYTES", str(24 * 1024 * 1024)))
MAX_TOOL_CHARS = int(os.environ.get("CODEX_LITE_MAX_TOOL_CHARS", "90000"))
MAX_SESSION_MESSAGES = int(os.environ.get("CODEX_LITE_MAX_SESSION_MESSAGES", "48"))
TASK_TAGS: dict[str, dict[str, Any]] = {
    "deep_research": {
        "label": "Глубокое исследование",
        "instruction": "Перед ответом глубже исследовать задачу: проверить несколько гипотез, собрать контекст из файлов и явно отделить выводы от предположений.",
    },
    "web_search": {
        "label": "Поиск в интернете",
        "instruction": "Если задача зависит от свежих фактов, документации, версий, цен, новостей или внешнего контекста, использовать web_search и добавить ссылки в итоговый ответ.",
    },
    "literary": {
        "label": "Литературный режим",
        "instruction": "Писать более живо и выразительно, сохраняя точность. Для кода и технических частей стиль остается строгим.",
        "temperature": 0.75,
    },
    "presentation": {
        "label": "Создание презентаций",
        "instruction": "Думать в формате презентации: структура слайдов, ключевой тезис каждого слайда, визуальная логика, короткие заголовки и понятная история.",
    },
    "spreadsheet": {
        "label": "Таблицы",
        "instruction": "Учитывать табличный формат: структуры данных, CSV/Excel, формулы, сводки, валидация и аккуратные названия колонок.",
    },
    "ux_review": {
        "label": "UX/UI-аудит",
        "instruction": "Оценивать пользовательские сценарии, визуальную иерархию, доступность, состояния интерфейса и реальные точки трения.",
    },
    "tests": {
        "label": "Тесты и регрессии",
        "instruction": "Особое внимание уделить проверкам, регрессиям, крайним случаям, воспроизводимости и минимальному набору тестов.",
    },
    "refactor": {
        "label": "Рефакторинг",
        "instruction": "Улучшать структуру кода консервативно: без лишней перестройки, с сохранением поведения и локальными правками.",
    },
}
FINAL_ANSWER_PROMPT = (
    "Останови использование инструментов. Напиши финальный ответ пользователю строго на русском языке, "
    "используя только уже собранный контекст и результаты инструментов. Не говори про лимит шагов инструментов, "
    "если это не критично для сути ответа. Если что-то осталось неизвестным, коротко укажи, что проверено и что осталось под вопросом."
)
TUNNEL_RECOVERY_LOCK = threading.Lock()
LAST_TUNNEL_RECOVERY_ATTEMPT = 0.0
DASHBOARD_CACHE: dict[str, Any] = {"timestamp": 0.0, "data": None}
DASHBOARD_LOCK = threading.Lock()
REQUEST_CONTEXT = threading.local()


def read_app_version() -> dict[str, Any]:
    candidates = [
        Path(os.environ.get("CODEX_LITE_VERSION_FILE") or "").expanduser() if os.environ.get("CODEX_LITE_VERSION_FILE") else None,
        APP_ROOT / "app-version.json",
        APP_DIR / "app-version.json",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            version = str(data.get("version") or "0.0.0")
            return {"version": version, "channel": str(data.get("channel") or "stable")}
    return {"version": "0.0.0", "channel": "stable"}


APP_VERSION_INFO = read_app_version()
APP_VERSION = str(APP_VERSION_INFO.get("version") or "0.0.0")


def version_tuple(value: str) -> tuple[int, int, int]:
    parts = re.findall(r"\d+", str(value or ""))
    numbers = [int(part) for part in parts[:3]]
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers[:3])  # type: ignore[return-value]


def current_update_platform() -> str:
    machine = platform.machine().lower()
    is_arm = machine in {"arm64", "aarch64"} or machine.startswith("arm")
    if os.name == "nt":
        return "windows-arm64" if is_arm else "windows-x64"
    if sys.platform == "darwin":
        return "macos-arm64" if is_arm else "macos-x64"
    if sys.platform.startswith("linux"):
        return "linux-arm64" if is_arm else "linux-x64"
    return f"{sys.platform}-{machine or 'unknown'}"


def update_platform_fallbacks(platform_id: str) -> list[str]:
    fallbacks = [platform_id]
    if platform_id == "windows-arm64":
        fallbacks.append("windows-x64")
    elif platform_id == "linux-arm64":
        fallbacks.append("linux-x64")
    elif platform_id == "macos-arm64":
        fallbacks.append("macos-x64")
    for fallback in ["windows-x64", "linux-x64", "macos-x64"]:
        if fallback not in fallbacks:
            fallbacks.append(fallback)
    return fallbacks


def validate_update_artifact(artifact: dict[str, Any], platform_id: str) -> dict[str, Any]:
    sha256 = str(artifact.get("sha256") or "").strip().lower()
    installer_url = str(artifact.get("installer_url") or artifact.get("url") or "").strip()
    filename = str(artifact.get("filename") or "").strip()
    parsed_url = urllib.parse.urlparse(installer_url)
    if not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise AppError(f"Update manifest contains an invalid SHA-256 hash for {platform_id}", 502)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise AppError(f"Update manifest contains an invalid installer URL for {platform_id}", 502)
    if filename and ("/" in filename or "\\" in filename or not re.fullmatch(r"[A-Za-z0-9._+-]+", filename)):
        raise AppError(f"Update manifest contains an unsafe filename for {platform_id}", 502)
    try:
        size_bytes = int(artifact.get("size_bytes") or 0)
    except (TypeError, ValueError) as exc:
        raise AppError(f"Update manifest contains an invalid installer size for {platform_id}", 502) from exc
    if size_bytes <= 0 or size_bytes > MAX_UPDATE_DOWNLOAD_BYTES:
        raise AppError(f"Update manifest contains an unsupported installer size for {platform_id}", 502)
    return artifact


def select_update_artifact(manifest: dict[str, Any]) -> dict[str, Any]:
    platform_id = current_update_platform()
    platforms = manifest.get("platforms")
    if isinstance(platforms, dict):
        for candidate in update_platform_fallbacks(platform_id):
            artifact = platforms.get(candidate)
            if isinstance(artifact, dict):
                selected = dict(artifact)
                selected["platform"] = candidate
                selected.setdefault("filename", default_update_filename(str(manifest.get("version") or "0.0.0"), candidate))
                return validate_update_artifact(selected, candidate)

    legacy = {
        "platform": "windows-x64",
        "filename": f"SerplexInstall_{manifest.get('version') or '0.0.0'}.exe",
        "installer_url": manifest.get("versioned_installer_url") or manifest.get("installer_url") or manifest.get("url"),
        "size_bytes": manifest.get("size_bytes"),
        "sha256": manifest.get("sha256"),
        "kind": "installer",
    }
    return validate_update_artifact(legacy, "windows-x64")


def default_update_filename(version: str, platform_id: str) -> str:
    if platform_id.startswith("windows"):
        return f"SerplexInstall_{version}.exe"
    if platform_id.startswith("macos"):
        arch = "arm64" if platform_id.endswith("arm64") else "x64"
        return f"Serplex_macos_{arch}_{version}.tar.gz"
    if platform_id.startswith("linux"):
        arch = "arm64" if platform_id.endswith("arm64") else "x64"
        return f"Serplex_linux_{arch}_{version}.tar.gz"
    return f"Serplex_{platform_id}_{version}.tar.gz"


def b64url_decode(value: str) -> bytes:
    raw = str(value or "").strip()
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("ascii"))


def canonical_update_payload_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def read_update_public_key() -> dict[str, Any]:
    try:
        data = json.loads(UPDATE_PUBLIC_KEY_FILE.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise AppError("Update public key is missing", 500) from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise AppError("Update public key is unreadable", 500) from exc
    if not isinstance(data, dict):
        raise AppError("Update public key must be a JSON object", 500)
    return data


def verify_rs256_signature(payload: dict[str, Any], signature: dict[str, Any]) -> str:
    public_key = read_update_public_key()
    if str(signature.get("alg") or "") != "RS256":
        raise AppError("Update manifest uses an unsupported signature algorithm", 502)
    key_id = str(signature.get("key_id") or "")
    expected_key_id = str(public_key.get("key_id") or "")
    if not key_id or key_id != expected_key_id:
        raise AppError("Update manifest was signed by an unknown key", 502)

    try:
        n = int.from_bytes(b64url_decode(str(public_key["n"])), "big")
        e = int.from_bytes(b64url_decode(str(public_key["e"])), "big")
        signature_bytes = b64url_decode(str(signature["value"]))
    except (KeyError, ValueError, TypeError, binascii.Error) as exc:
        raise AppError("Update signature is malformed", 502) from exc

    key_bytes = (n.bit_length() + 7) // 8
    if not signature_bytes or len(signature_bytes) != key_bytes:
        raise AppError("Update signature size does not match the public key", 502)

    encoded = pow(int.from_bytes(signature_bytes, "big"), e, n).to_bytes(key_bytes, "big")
    digest = hashlib.sha256(canonical_update_payload_bytes(payload)).digest()
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + digest
    separator = encoded.find(b"\x00", 2)
    valid = (
        encoded.startswith(b"\x00\x01")
        and separator >= 10
        and all(byte == 0xFF for byte in encoded[2:separator])
        and hmac.compare_digest(encoded[separator + 1 :], digest_info)
    )
    if not valid:
        raise AppError("Update manifest signature is invalid", 502)
    return key_id


def validate_update_payload(payload: dict[str, Any]) -> dict[str, Any]:
    version = str(payload.get("version") or "").strip()
    if not version:
        raise AppError("Update manifest does not contain a version", 502)
    platforms = payload.get("platforms")
    if isinstance(platforms, dict):
        if not platforms:
            raise AppError("Update manifest contains no platform artifacts", 502)
        for platform_id, artifact in platforms.items():
            if not isinstance(artifact, dict):
                raise AppError(f"Update manifest contains an invalid artifact for {platform_id}", 502)
            validate_update_artifact(artifact, str(platform_id))
    if payload.get("installer_url") or payload.get("url") or payload.get("sha256") or payload.get("size_bytes"):
        validate_update_artifact(
            {
                "installer_url": payload.get("installer_url") or payload.get("url"),
                "size_bytes": payload.get("size_bytes"),
                "sha256": payload.get("sha256"),
                "filename": payload.get("filename"),
            },
            "windows-x64",
        )
    elif not isinstance(platforms, dict):
        raise AppError("Update manifest does not contain a downloadable artifact", 502)
    return payload


def verify_update_manifest(data: dict[str, Any]) -> dict[str, Any]:
    if isinstance(data.get("payload"), dict) and isinstance(data.get("signature"), dict):
        payload = validate_update_payload(data["payload"])
        key_id = verify_rs256_signature(payload, data["signature"])
        return {**payload, "signature_verified": True, "signature_key_id": key_id}
    if ALLOW_UNSIGNED_UPDATES:
        payload = validate_update_payload(data)
        return {**payload, "signature_verified": False, "signature_key_id": ""}
    raise AppError("Update manifest is not signed", 502)


def fetch_update_manifest() -> dict[str, Any]:
    if not UPDATE_MANIFEST_URL:
        raise AppError("Update manifest URL is not configured", 404)
    request = urllib.request.Request(UPDATE_MANIFEST_URL, method="GET")
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AppError(f"Update manifest HTTP {exc.code}: {detail}", 502) from exc
    except urllib.error.URLError as exc:
        raise AppError(f"Update manifest is not reachable: {exc}", 502) from exc
    try:
        data = json.loads(raw.decode("utf-8-sig", errors="replace"))
    except json.JSONDecodeError as exc:
        raise AppError("Update manifest returned invalid JSON", 502) from exc
    if not isinstance(data, dict):
        raise AppError("Update manifest must be a JSON object", 502)
    return verify_update_manifest(data)


def update_status() -> dict[str, Any]:
    current = APP_VERSION
    try:
        manifest = fetch_update_manifest()
    except AppError as exc:
        return {
            "ok": False,
            "current_version": current,
            "manifest_url": UPDATE_MANIFEST_URL,
            "update_available": False,
            "error": str(exc),
        }
    latest = str(manifest.get("version") or "0.0.0")
    update_available = version_tuple(latest) > version_tuple(current)
    try:
        artifact = select_update_artifact(manifest)
    except AppError as exc:
        return {
            "ok": False,
            "current_version": current,
            "latest_version": latest,
            "manifest_url": UPDATE_MANIFEST_URL,
            "update_available": False,
            "error": str(exc),
        }
    installer_url = str(artifact.get("installer_url") or artifact.get("url") or "")
    verified_download_url = "/api/update/download" if update_available and installer_url else ""
    return {
        "ok": True,
        "current_version": current,
        "latest_version": latest,
        "platform": artifact.get("platform") or current_update_platform(),
        "manifest_url": UPDATE_MANIFEST_URL,
        "update_available": update_available,
        "installer_url": verified_download_url,
        "download_url": verified_download_url,
        "filename": artifact.get("filename") or "",
        "size_bytes": artifact.get("size_bytes"),
        "sha256": artifact.get("sha256"),
        "published_at": manifest.get("published_at"),
        "notes": manifest.get("notes") or "",
        "changes": manifest.get("changes") if isinstance(manifest.get("changes"), list) else [],
        "signature": {
            "verified": bool(manifest.get("signature_verified")),
            "key_id": manifest.get("signature_key_id") or "",
        },
    }


def update_cache_dir() -> Path:
    path = USER_DATA_DIR / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def download_verified_update_file() -> tuple[Path, str, int]:
    manifest = fetch_update_manifest()
    latest = str(manifest.get("version") or "0.0.0")
    if version_tuple(latest) <= version_tuple(APP_VERSION):
        raise AppError("No newer update is available", 409)
    artifact = select_update_artifact(manifest)
    installer_url = str(artifact.get("versioned_installer_url") or artifact.get("installer_url") or artifact.get("url") or "")
    expected_sha256 = str(artifact.get("sha256") or "").lower()
    expected_size = int(artifact.get("size_bytes") or 0)
    filename = str(artifact.get("filename") or default_update_filename(latest, str(artifact.get("platform") or current_update_platform())))
    updates_dir = update_cache_dir()
    fd, temp_name = tempfile.mkstemp(prefix="SerplexUpdate-", suffix=".download", dir=str(updates_dir))
    temp_path = Path(temp_name)
    os.close(fd)

    request = urllib.request.Request(installer_url, method="GET")
    request.add_header("Accept", "application/octet-stream")
    sha = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temp_path.open("wb") as target:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPDATE_DOWNLOAD_BYTES:
                    raise AppError("Update installer is too large", 502)
                sha.update(chunk)
                target.write(chunk)
    except urllib.error.HTTPError as exc:
        temp_path.unlink(missing_ok=True)
        detail = exc.read().decode("utf-8", errors="replace")
        raise AppError(f"Update installer HTTP {exc.code}: {detail}", 502) from exc
    except urllib.error.URLError as exc:
        temp_path.unlink(missing_ok=True)
        raise AppError(f"Update installer is not reachable: {exc}", 502) from exc
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    actual_sha256 = sha.hexdigest()
    if total != expected_size:
        temp_path.unlink(missing_ok=True)
        raise AppError("Update installer size does not match the signed manifest", 502)
    if not hmac.compare_digest(actual_sha256, expected_sha256):
        temp_path.unlink(missing_ok=True)
        raise AppError("Update installer hash does not match the signed manifest", 502)
    final_path = updates_dir / filename
    final_path.unlink(missing_ok=True)
    os.replace(temp_path, final_path)
    return final_path, filename, total


def desktop_update_style() -> str:
    return (os.environ.get("SERPLEX_UPDATE_STYLE") or "").strip().lower()


def desktop_update_target() -> str:
    return (os.environ.get("SERPLEX_UPDATE_TARGET") or "").strip()


def desktop_process_pid() -> str:
    raw = (os.environ.get("SERPLEX_DESKTOP_PID") or "").strip()
    return raw if raw.isdigit() else ""


def install_tauri_bundle_update(installer_path: Path, filename: str, size: int, style: str, target: str) -> dict[str, Any]:
    if not filename.endswith(".tar.gz"):
        raise AppError("This platform expects a signed .tar.gz Tauri update package.", 502)
    if not target:
        raise AppError("Tauri update target is not configured.", 500)

    helper_path = update_cache_dir() / f"run-tauri-update-{uuid.uuid4().hex}.sh"
    server_pid = str(os.getpid())
    desktop_pid = desktop_process_pid()

    if style == "linux-appimage":
        script = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"ARCHIVE={shlex.quote(str(installer_path))}\n"
            f"TARGET={shlex.quote(target)}\n"
            f"SERVER_PID={shlex.quote(server_pid)}\n"
            f"DESKTOP_PID={shlex.quote(desktop_pid)}\n"
            "TMP=$(mktemp -d)\n"
            "cleanup() { rm -rf \"$TMP\"; rm -f \"$0\"; }\n"
            "trap cleanup EXIT\n"
            "tar -xzf \"$ARCHIVE\" -C \"$TMP\"\n"
            "SRC=$(find \"$TMP\" -type f \\( -name '*.AppImage' -o -name 'Serplex.AppImage' \\) | head -n 1)\n"
            "if [ -z \"${SRC:-}\" ]; then echo 'AppImage not found in update package' >&2; exit 1; fi\n"
            "chmod +x \"$SRC\"\n"
            "NEXT=\"$TARGET.next\"\n"
            "cp \"$SRC\" \"$NEXT\"\n"
            "chmod +x \"$NEXT\"\n"
            "kill \"$SERVER_PID\" >/dev/null 2>&1 || true\n"
            "if [ -n \"$DESKTOP_PID\" ]; then kill \"$DESKTOP_PID\" >/dev/null 2>&1 || true; fi\n"
            "sleep 2\n"
            "mv \"$NEXT\" \"$TARGET\"\n"
            "nohup \"$TARGET\" >/dev/null 2>&1 &\n"
        )
    elif style == "macos-app":
        script = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"ARCHIVE={shlex.quote(str(installer_path))}\n"
            f"TARGET={shlex.quote(target)}\n"
            f"SERVER_PID={shlex.quote(server_pid)}\n"
            f"DESKTOP_PID={shlex.quote(desktop_pid)}\n"
            "TMP=$(mktemp -d)\n"
            "cleanup() { rm -rf \"$TMP\"; rm -f \"$0\"; }\n"
            "trap cleanup EXIT\n"
            "tar -xzf \"$ARCHIVE\" -C \"$TMP\"\n"
            "SRC=$(find \"$TMP\" -maxdepth 3 -type d -name 'Serplex.app' | head -n 1)\n"
            "if [ -z \"${SRC:-}\" ]; then echo 'Serplex.app not found in update package' >&2; exit 1; fi\n"
            "PARENT=$(dirname \"$TARGET\")\n"
            "NEXT=\"$PARENT/Serplex.app.next\"\n"
            "BACKUP=\"$PARENT/Serplex.app.previous\"\n"
            "rm -rf \"$NEXT\" \"$BACKUP\"\n"
            "cp -R \"$SRC\" \"$NEXT\"\n"
            "chmod +x \"$NEXT/Contents/MacOS/\"* >/dev/null 2>&1 || true\n"
            "kill \"$SERVER_PID\" >/dev/null 2>&1 || true\n"
            "if [ -n \"$DESKTOP_PID\" ]; then kill \"$DESKTOP_PID\" >/dev/null 2>&1 || true; fi\n"
            "sleep 2\n"
            "if [ -d \"$TARGET\" ]; then mv \"$TARGET\" \"$BACKUP\"; fi\n"
            "mv \"$NEXT\" \"$TARGET\"\n"
            "rm -rf \"$BACKUP\"\n"
            "nohup open \"$TARGET\" >/dev/null 2>&1 &\n"
        )
    else:
        raise AppError(f"Unsupported Tauri update style: {style}", 501)

    helper_path.write_text(script, encoding="utf-8")
    helper_path.chmod(0o700)
    subprocess.Popen(
        ["/bin/sh", str(helper_path)],
        cwd=str(update_cache_dir()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
    return {
        "ok": True,
        "installing": True,
        "message": "Обновление скачано и устанавливается. Приложение перезапустится автоматически.",
        "installer": filename,
        "size_bytes": size,
    }


def install_verified_update() -> dict[str, Any]:
    installer_path, filename, size = download_verified_update_file()
    if not installer_path.exists():
        raise AppError("Проверенный файл обновления не найден после скачивания.", 500)

    if os.name == "nt":
        helper_path = update_cache_dir() / f"run-update-{uuid.uuid4().hex}.cmd"
        helper_path.write_text(
            "@echo off\r\n"
            "setlocal\r\n"
            "timeout /t 1 /nobreak >nul\r\n"
            f"start \"\" \"{installer_path}\" /silent /self-update\r\n"
            "del \"%~f0\" >nul 2>nul\r\n",
            encoding="ascii",
        )
        creationflags = 0
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "DETACHED_PROCESS"):
            creationflags |= subprocess.DETACHED_PROCESS
        subprocess.Popen(
            ["cmd.exe", "/c", str(helper_path)],
            cwd=str(update_cache_dir()),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
        return {
            "ok": True,
            "installing": True,
            "message": "Обновление скачано и устанавливается. Приложение перезапустится автоматически.",
            "installer": filename,
            "size_bytes": size,
        }

    style = desktop_update_style()
    target = desktop_update_target()
    if style in {"linux-appimage", "macos-app"}:
        return install_tauri_bundle_update(installer_path, filename, size, style, target)

    if not (sys.platform == "darwin" or sys.platform.startswith("linux")):
        raise AppError("Автоустановка обновлений поддерживается только на Windows, macOS и Linux.", 501)
    if not filename.endswith(".tar.gz"):
        raise AppError("Для этой системы обновление должно быть архивом .tar.gz.", 502)

    helper_path = update_cache_dir() / f"run-update-{uuid.uuid4().hex}.sh"
    launcher = "serplex.command" if sys.platform == "darwin" else "serplex.sh"
    helper_path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"ARCHIVE={shlex.quote(str(installer_path))}\n"
        f"TARGET={shlex.quote(str(APP_ROOT))}\n"
        f"LAUNCHER={shlex.quote(launcher)}\n"
        f"SERVER_PID={os.getpid()}\n"
        f"DESKTOP_PID={shlex.quote(desktop_process_pid())}\n"
        "sleep 2\n"
        "kill \"$SERVER_PID\" >/dev/null 2>&1 || true\n"
        "if [ -n \"$DESKTOP_PID\" ]; then kill \"$DESKTOP_PID\" >/dev/null 2>&1 || true; fi\n"
        "sleep 1\n"
        "TMP=$(mktemp -d)\n"
        "cleanup() { rm -rf \"$TMP\"; rm -f \"$0\"; }\n"
        "trap cleanup EXIT\n"
        "tar -xzf \"$ARCHIVE\" -C \"$TMP\"\n"
        "SRC=\"$TMP/Serplex\"\n"
        "if [ ! -d \"$SRC\" ]; then SRC=$(find \"$TMP\" -mindepth 1 -maxdepth 1 -type d | head -n 1); fi\n"
        "if [ ! -d \"$SRC\" ]; then exit 1; fi\n"
        "if command -v rsync >/dev/null 2>&1; then\n"
        "  rsync -a --delete --exclude 'codex_local/.runtime' \"$SRC\"/ \"$TARGET\"/\n"
        "else\n"
        "  cp -R \"$SRC\"/. \"$TARGET\"/\n"
        "fi\n"
        "chmod +x \"$TARGET/serplex.sh\" \"$TARGET/serplex.command\" \"$TARGET/install.sh\" >/dev/null 2>&1 || true\n"
        "nohup \"$TARGET/$LAUNCHER\" >/dev/null 2>&1 &\n",
        encoding="utf-8",
    )
    helper_path.chmod(0o700)
    subprocess.Popen(
        ["/bin/sh", str(helper_path)],
        cwd=str(update_cache_dir()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
    return {
        "ok": True,
        "installing": True,
        "message": "Обновление скачано и устанавливается. Приложение перезапустится автоматически.",
        "installer": filename,
        "size_bytes": size,
    }

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".aider.tags.cache.v4",
    ".codex-lite",
    ".venv",
    ".venv-aider",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".cache",
}

BINARY_EXTENSIONS = {
    ".7z",
    ".bin",
    ".bmp",
    ".class",
    ".dll",
    ".exe",
    ".gif",
    ".ico",
    ".jar",
    ".jpg",
    ".jpeg",
    ".mov",
    ".mp3",
    ".mp4",
    ".obj",
    ".pdf",
    ".png",
    ".pyc",
    ".rar",
    ".so",
    ".wasm",
    ".webp",
    ".zip",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

class AppError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class ClientDisconnected(Exception):
    pass


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def json_dumps(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=None, separators=(",", ":")).encode("utf-8")


def safe_user_id(value: str | None = None) -> str:
    raw = str(value or "").strip()
    if not raw:
        raw = DEFAULT_USER_ID
    safe = re.sub(r"[^a-zA-Z0-9_.@-]+", "-", raw)[:80].strip(".-")
    return safe or DEFAULT_USER_ID


def current_user_id() -> str:
    return safe_user_id(getattr(REQUEST_CONTEXT, "user_id", DEFAULT_USER_ID))


def set_current_user(value: str | None) -> None:
    REQUEST_CONTEXT.user_id = safe_user_id(value)


def user_state_dir(user_id: str | None = None) -> Path:
    return USERS_DIR / safe_user_id(user_id or current_user_id())


def user_state_file(user_id: str | None = None) -> Path:
    return user_state_dir(user_id) / "state.json"


def recent_projects_file(user_id: str | None = None) -> Path:
    return user_state_dir(user_id) / "recent-projects.json"


def general_chat_dir(user_id: str | None = None) -> Path:
    return user_state_dir(user_id) / "general-chats"


def load_user_state(user_id: str | None = None) -> dict[str, Any]:
    try:
        data = json.loads(user_state_file(user_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_user_state(data: dict[str, Any], user_id: str | None = None) -> None:
    directory = user_state_dir(user_id)
    directory.mkdir(parents=True, exist_ok=True)
    data = {**data, "user_id": safe_user_id(user_id or current_user_id()), "updated_at": now_iso()}
    user_state_file(user_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_user_workspace(user_id: str | None = None) -> Path | None:
    path_value = load_user_state(user_id).get("workspace")
    if not path_value:
        return None
    try:
        return Path(str(path_value)).expanduser().resolve()
    except OSError:
        return None


def set_user_workspace(path: Path | None, user_id: str | None = None) -> None:
    state = load_user_state(user_id)
    if path is None:
        state.pop("workspace", None)
    else:
        state["workspace"] = str(path.expanduser().resolve())
    save_user_state(state, user_id)


def set_workspace_override(path: Path | None) -> None:
    REQUEST_CONTEXT.workspace_override_set = True
    REQUEST_CONTEXT.workspace_override = path.resolve() if path else None


def apply_payload_workspace(payload: dict[str, Any]) -> None:
    if "project_path" not in payload:
        return
    path_value = str(payload.get("project_path") or "").strip()
    if not path_value:
        set_workspace_override(None)
        return
    root = Path(path_value).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise AppError(f"Project folder not found: {root}", 404)
    set_workspace_override(root)


def ollama_url(path: str = "") -> str:
    return f"{OLLAMA_BASE_URL}{path}"


def model_endpoint_requires_api_key() -> bool:
    parsed = urllib.parse.urlparse(OLLAMA_BASE_URL)
    host = (parsed.hostname or "").lower()
    return host not in {"", "127.0.0.1", "localhost", "::1"}


def require_model_api_key() -> None:
    if model_endpoint_requires_api_key() and not MODEL_API_KEY.strip():
        raise AppError(
            "Невалидный API-ключ. Требуется новый ключ в настройках.",
            401,
        )


def raise_ollama_http_error(exc: urllib.error.HTTPError) -> None:
    detail = exc.read().decode("utf-8", errors="replace")
    if exc.code in {401, 403}:
        raise AppError("Невалидный API-ключ. Требуется новый ключ в настройках.", 401) from exc
    raise AppError(f"Ollama HTTP {exc.code}: {detail}", 502) from exc


def add_ollama_headers(request: urllib.request.Request) -> urllib.request.Request:
    if MODEL_API_KEY:
        request.add_header("X-Serplex-Key", MODEL_API_KEY)
        request.add_header("X-Local-Codex-Key", MODEL_API_KEY)
        request.add_header("Authorization", f"Bearer {MODEL_API_KEY}")
    return request


def ollama_local_port() -> int:
    parsed = urllib.parse.urlparse(OLLAMA_BASE_URL)
    return parsed.port or (443 if parsed.scheme == "https" else 80)


def can_auto_start_tunnel() -> bool:
    parsed = urllib.parse.urlparse(OLLAMA_BASE_URL)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", ""} and host in {"127.0.0.1", "localhost", "::1"} and TUNNEL_SCRIPT.exists()


def is_ollama_ready(timeout: int = 3) -> bool:
    request = urllib.request.Request(ollama_url("/api/version"), method="GET")
    add_ollama_headers(request)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 500
    except Exception:
        return False


def try_recover_ollama_endpoint() -> bool:
    global LAST_TUNNEL_RECOVERY_ATTEMPT
    if not can_auto_start_tunnel():
        return False
    with TUNNEL_RECOVERY_LOCK:
        if is_ollama_ready(timeout=2):
            return True
        now = time.time()
        if now - LAST_TUNNEL_RECOVERY_ATTEMPT < 8:
            return is_ollama_ready(timeout=2)
        LAST_TUNNEL_RECOVERY_ATTEMPT = now

        log_dir = APP_DIR.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout = (log_dir / "local-codex-auto-tunnel.out.log").open("ab")
        stderr = (log_dir / "local-codex-auto-tunnel.err.log").open("ab")
        args = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(TUNNEL_SCRIPT),
            "-ListenAddress",
            "127.0.0.1",
            "-LocalPort",
            str(ollama_local_port()),
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.Popen(args, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, creationflags=creationflags)
            stdout.close()
            stderr.close()
        except OSError:
            stdout.close()
            stderr.close()
            return False

        for _ in range(45):
            if is_ollama_ready(timeout=2):
                return True
            time.sleep(1)
        return False


def truncate(value: str, limit: int = MAX_TOOL_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n\n[truncated {len(value) - limit} chars]"


def normalize_task_tags(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    tags: list[str] = []
    for item in raw:
        tag_id = str(item or "").strip()
        if tag_id in TASK_TAGS and tag_id not in tags:
            tags.append(tag_id)
    return tags


def task_tags_summary(tags: list[str]) -> str:
    if not tags:
        return ""
    lines = ["Режимы задачи:"]
    for tag_id in tags:
        tag = TASK_TAGS[tag_id]
        lines.append(f"- {tag['label']}: {tag['instruction']}")
    return "\n".join(lines)


def task_temperature(tags: list[str]) -> float:
    values = [float(TASK_TAGS[tag].get("temperature", 0.15)) for tag in tags if tag in TASK_TAGS]
    return max(values) if values else 0.15


def has_full_access() -> bool:
    state = load_user_state()
    if "full_access" in state:
        return bool(state.get("full_access"))
    return FULL_ACCESS_ENABLED


def has_workspace() -> bool:
    return workspace_root(required=False) is not None


def workspace_root(required: bool = True) -> Path | None:
    if bool(getattr(REQUEST_CONTEXT, "workspace_override_set", False)):
        root = getattr(REQUEST_CONTEXT, "workspace_override", None)
    else:
        root = get_user_workspace()
    if root is None and required:
        raise AppError("Проект не выбран. Откройте папку проекта или задайте общий вопрос без работы с файлами.", 409)
    return root


def project_name() -> str:
    root = workspace_root(required=False)
    if root is None:
        return "Без проекта"
    return root.name or str(root)


def chat_dir() -> Path:
    root = workspace_root(required=False)
    return chat_dir_for(root) if root else general_chat_dir()


def chat_dir_for(root: Path) -> Path:
    return root / ".codex-lite" / "users" / current_user_id() / "chats"


def project_summary(path: Path | None = None) -> dict[str, Any]:
    if path is None and not has_workspace():
        return {"name": "Без проекта", "path": "", "chat_store": str(general_chat_dir()), "exists": False, "no_project": True}
    root = (path or workspace_root()).resolve()
    return {
        "name": root.name or str(root),
        "path": str(root),
        "chat_store": str(chat_dir_for(root)),
        "exists": root.exists(),
    }


def general_project_summary() -> dict[str, Any]:
    return {
        "name": "Без проекта",
        "path": "",
        "chat_store": str(general_chat_dir()),
        "exists": True,
        "no_project": True,
        "current": workspace_root(required=False) is None,
        "chats": list_chat_records_in_dir(general_chat_dir()),
    }


def load_recent_projects() -> list[dict[str, Any]]:
    try:
        data = json.loads(recent_projects_file().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = []
    projects = []
    seen: set[str] = set()
    for item in data if isinstance(data, list) else []:
        path_value = item.get("path") if isinstance(item, dict) else None
        if not path_value:
            continue
        try:
            path = Path(path_value).expanduser().resolve()
        except OSError:
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        projects.append(
            {
                "name": item.get("name") or path.name or str(path),
                "path": str(path),
                "last_opened": item.get("last_opened"),
                "exists": path.exists() and path.is_dir(),
            }
        )
    return projects[:20]


def save_recent_projects(projects: list[dict[str, Any]]) -> None:
    user_state_dir().mkdir(parents=True, exist_ok=True)
    recent_projects_file().write_text(json.dumps(projects[:20], ensure_ascii=False, indent=2), encoding="utf-8")


def project_path_key(path: Path | str) -> str:
    if not str(path or "").strip():
        return ""
    return str(Path(path).expanduser().resolve()).lower()


def remember_project(path: Path) -> None:
    resolved = path.resolve()
    key = project_path_key(resolved)
    recent = load_recent_projects()
    entry = {
        "name": resolved.name or str(resolved),
        "path": str(resolved),
        "last_opened": now_iso(),
        "exists": True,
    }
    for index, item in enumerate(recent):
        if project_path_key(item.get("path", "")) == key:
            recent[index] = {**item, **entry}
            break
    else:
        recent.insert(0, entry)
    save_recent_projects(recent)


def forget_project(path_value: str) -> dict[str, Any]:
    if not path_value or not str(path_value).strip():
        raise AppError("Project path is required", 400)
    target = Path(str(path_value).strip()).expanduser().resolve()
    target_key = project_path_key(target)
    recent = [item for item in load_recent_projects() if project_path_key(item.get("path", "")) != target_key]
    save_recent_projects(recent)
    current_root = workspace_root(required=False)
    removed_current = bool(current_root and project_path_key(current_root) == target_key)
    if removed_current:
        set_user_workspace(None)
    projects = list_projects_with_chats()
    return {
        "ok": True,
        "removed": str(target),
        "removed_current": removed_current,
        "project": project_summary(),
        "projects": projects,
        "recent": projects,
        "chats": list_chat_records(),
    }


def open_project(path_value: str) -> dict[str, Any]:
    if not path_value or not str(path_value).strip():
        set_user_workspace(None)
        return project_summary()
    path = Path(str(path_value).strip()).expanduser()
    if not path.is_absolute():
        base = workspace_root(required=False) or CWD_WORKSPACE
        path = (base / path)
    resolved = path.resolve()
    if not resolved.exists():
        raise AppError(f"Folder does not exist: {resolved}", 404)
    if not resolved.is_dir():
        raise AppError(f"Path is not a folder: {resolved}", 400)
    set_user_workspace(resolved)
    remember_project(resolved)
    return project_summary(resolved)


def get_roots() -> list[dict[str, Any]]:
    roots: list[dict[str, Any]] = []
    if os.name == "nt":
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(f"{letter}:\\")
            if drive.exists():
                roots.append({"name": f"{letter}:\\", "path": str(drive), "type": "drive"})
    else:
        roots.append({"name": "/", "path": "/", "type": "root"})
        home = Path.home()
        if home.exists():
            roots.append({"name": home.name or str(home), "path": str(home), "type": "home"})
    return roots


def browse_folders(path_value: str | None = None) -> dict[str, Any]:
    if not path_value:
        return {"path": "", "parent": None, "roots": get_roots(), "folders": []}
    path = Path(path_value).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise AppError(f"Folder not found: {path}", 404)
    folders: list[dict[str, Any]] = []
    try:
        children = sorted(path.iterdir(), key=lambda item: item.name.lower())
    except OSError as exc:
        raise AppError(str(exc), 403) from exc
    for child in children:
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name in {"$Recycle.Bin", "System Volume Information", "__pycache__", "node_modules"}:
            continue
        try:
            folders.append(
                {
                    "name": child.name,
                    "path": str(child.resolve()),
                    "has_chats": chat_dir_for(child).exists(),
                }
            )
        except OSError:
            continue
        if len(folders) >= 250:
            break
    parent = str(path.parent) if path.parent != path else None
    return {"path": str(path), "parent": parent, "roots": get_roots(), "folders": folders}


def pick_folder_native(initial_path: str | None = None) -> str | None:
    if os.name != "nt":
        initial = str(Path(initial_path).expanduser().resolve()) if initial_path else str(workspace_root(required=False) or Path.home())
        if sys.platform == "darwin":
            script = 'POSIX path of (choose folder with prompt "Выберите папку проекта")'
            completed = subprocess.run(
                ["osascript", "-e", script],
                cwd=initial if Path(initial).is_dir() else str(Path.home()),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3600,
                check=False,
            )
            if completed.returncode == 1:
                return None
            if completed.returncode != 0:
                raise AppError((completed.stderr or completed.stdout or "Could not open macOS folder picker").strip(), 500)
            return completed.stdout.strip() or None
        if sys.platform.startswith("linux"):
            command = shutil.which("zenity")
            if command:
                completed = subprocess.run(
                    [command, "--file-selection", "--directory", "--title=Выберите папку проекта", f"--filename={initial}/"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=3600,
                    check=False,
                )
                if completed.returncode in {1, 5}:
                    return None
                if completed.returncode != 0:
                    raise AppError((completed.stderr or completed.stdout or "Could not open Linux folder picker").strip(), 500)
                return completed.stdout.strip() or None
            command = shutil.which("kdialog")
            if command:
                completed = subprocess.run(
                    [command, "--getexistingdirectory", initial],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=3600,
                    check=False,
                )
                if completed.returncode:
                    return None
                return completed.stdout.strip() or None
        raise AppError("Native folder picker requires osascript on macOS or zenity/kdialog on Linux", 501)
    initial = str(Path(initial_path).expanduser().resolve()) if initial_path else str(workspace_root(required=False) or Path.home())
    script = r"""
$Initial = $env:CODEX_LITE_PICKER_INITIAL
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'Выберите папку проекта'
$dialog.CheckFileExists = $false
$dialog.CheckPathExists = $true
$dialog.ValidateNames = $false
$dialog.DereferenceLinks = $true
$dialog.FileName = 'Выберите эту папку'
if ($Initial -and (Test-Path -LiteralPath $Initial -PathType Container)) {
  $dialog.InitialDirectory = $Initial
}
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.StartPosition = 'CenterScreen'
$owner.Width = 1
$owner.Height = 1
$owner.Opacity = 0
$owner.Show()
$owner.Activate()
$result = $dialog.ShowDialog($owner)
$owner.Close()
$owner.Dispose()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  $selected = Split-Path -Parent $dialog.FileName
  Write-Output $selected
}
$dialog.Dispose()
"""
    env = os.environ.copy()
    env["CODEX_LITE_PICKER_INITIAL"] = initial
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise AppError(f"Could not open Windows folder picker: {detail}", 500)
    picked = completed.stdout.strip()
    return picked or None


def pick_file_native(initial_path: str | None = None) -> dict[str, str] | None:
    if os.name != "nt":
        initial = str(Path(initial_path).expanduser().resolve()) if initial_path else str(workspace_root(required=False) or Path.home())
        picked = ""
        if sys.platform == "darwin":
            script = 'POSIX path of (choose file with prompt "Выберите файл для контекста")'
            completed = subprocess.run(
                ["osascript", "-e", script],
                cwd=initial if Path(initial).is_dir() else str(Path.home()),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3600,
                check=False,
            )
            if completed.returncode == 1:
                return None
            if completed.returncode != 0:
                raise AppError((completed.stderr or completed.stdout or "Could not open macOS file picker").strip(), 500)
            picked = completed.stdout.strip()
        elif sys.platform.startswith("linux"):
            command = shutil.which("zenity")
            if command:
                completed = subprocess.run(
                    [command, "--file-selection", "--title=Выберите файл для контекста", f"--filename={initial}/"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=3600,
                    check=False,
                )
                if completed.returncode in {1, 5}:
                    return None
                if completed.returncode != 0:
                    raise AppError((completed.stderr or completed.stdout or "Could not open Linux file picker").strip(), 500)
                picked = completed.stdout.strip()
            else:
                command = shutil.which("kdialog")
                if command:
                    completed = subprocess.run(
                        [command, "--getopenfilename", initial],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=3600,
                        check=False,
                    )
                    if completed.returncode:
                        return None
                    picked = completed.stdout.strip()
        if not picked:
            raise AppError("Native file picker requires osascript on macOS or zenity/kdialog on Linux", 501)
        target = resolve_user_path(picked)
        return {"path": str(target), "name": target.name, "size": target.stat().st_size}
    initial = str(Path(initial_path).expanduser().resolve()) if initial_path else str(workspace_root(required=False) or Path.home())
    script = r"""
$Initial = $env:CODEX_LITE_PICKER_INITIAL
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'Выберите файл для контекста'
$dialog.CheckFileExists = $true
$dialog.CheckPathExists = $true
$dialog.DereferenceLinks = $true
$dialog.Multiselect = $false
$dialog.Filter = 'Все файлы (*.*)|*.*'
if ($Initial -and (Test-Path -LiteralPath $Initial -PathType Container)) {
  $dialog.InitialDirectory = $Initial
}
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.StartPosition = 'CenterScreen'
$owner.Width = 1
$owner.Height = 1
$owner.Opacity = 0
$owner.Show()
$owner.Activate()
$result = $dialog.ShowDialog($owner)
$owner.Close()
$owner.Dispose()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  Write-Output $dialog.FileName
}
$dialog.Dispose()
"""
    env = os.environ.copy()
    env["CODEX_LITE_PICKER_INITIAL"] = initial
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise AppError(f"Could not open Windows file picker: {detail}", 500)
    picked = completed.stdout.strip()
    if not picked:
        return None
    target = resolve_user_path(picked)
    if not target.exists() or not target.is_file():
        raise AppError(f"File not found: {picked}", 404)
    return {"path": str(target), "name": target.name, "relative_path": to_rel(target)}


def resolve_user_path(path_value: str | None = None, allow_full_access: bool | None = None) -> Path:
    raw = (path_value or ".").strip() or "."
    path = Path(raw).expanduser()
    if not path.is_absolute():
        root = workspace_root(required=False)
        if root is None:
            raise AppError("Проект не выбран, поэтому файловые операции недоступны.", 409)
        path = root / path
    resolved = path.resolve()
    full_access = has_full_access() if allow_full_access is None else allow_full_access
    if full_access:
        return resolved
    root = workspace_root(required=False)
    if root is None:
        raise AppError("Проект не выбран, поэтому файловые операции недоступны.", 409)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AppError(f"Path escapes workspace: {raw}", 403) from exc
    return resolved


def to_rel(path: Path) -> str:
    root = workspace_root(required=False)
    if root is None:
        return str(path)
    try:
        rel = path.resolve().relative_to(root)
        value = rel.as_posix()
        return "." if value == "" else value
    except ValueError:
        return str(path)


def is_hidden(path: Path) -> bool:
    root = workspace_root()
    return any(part.startswith(".") for part in path.relative_to(root).parts if part not in {".", ""})


def is_skipped_dir(path: Path) -> bool:
    return path.name in IGNORED_DIRS


def looks_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    try:
        with path.open("rb") as handle:
            sample = handle.read(4096)
        return b"\0" in sample
    except OSError:
        return True


def read_text_file(path: Path) -> str:
    if not path.exists():
        raise AppError(f"File not found: {to_rel(path)}", 404)
    if not path.is_file():
        raise AppError(f"Not a file: {to_rel(path)}", 400)
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise AppError(f"File is too large to read ({size} bytes, limit {MAX_FILE_BYTES})", 413)
    if looks_binary(path):
        raise AppError(f"Binary file is not readable as text: {to_rel(path)}", 415)
    return path.read_text(encoding="utf-8", errors="replace")


def normalize_attachments(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    attachments: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw[:8]:
        if isinstance(item, dict):
            raw_path = str(item.get("path") or "").strip()
            name = str(item.get("name") or "").strip()
        else:
            raw_path = str(item or "").strip()
            name = ""
        if not raw_path:
            continue
        try:
            resolved = resolve_user_path(raw_path)
        except Exception:
            continue
        key = str(resolved).lower()
        if key in seen or not resolved.is_file():
            continue
        seen.add(key)
        attachments.append(
            {
                "path": str(resolved),
                "name": name or resolved.name,
                "relative_path": to_rel(resolved),
            }
        )
    return attachments


def attachment_context(attachments: list[dict[str, str]]) -> str:
    if not attachments:
        return ""
    blocks: list[str] = ["Прикреплённые файлы:"]
    remaining = 45000
    for item in attachments:
        path = item.get("path") or ""
        if not path:
            continue
        target = resolve_user_path(path)
        label = item.get("relative_path") or to_rel(target)
        try:
            content = read_text_file(target)
        except AppError as exc:
            blocks.append(f"\n### {label}\nФайл не прочитан: {exc}")
            continue
        if remaining <= 0:
            blocks.append(f"\n### {label}\nКонтекст не добавлен: общий лимит прикреплённых файлов исчерпан.")
            continue
        clipped = truncate(content, remaining)
        remaining -= len(clipped)
        blocks.append(f"\n### {label}\n```text\n{clipped}\n```")
    return "\n".join(blocks)


def build_user_content(message: str, task_tags: list[str] | None = None, attachments: list[dict[str, str]] | None = None, current_file: Any = None) -> str:
    parts = [message]
    tag_context = task_tags_summary(task_tags or [])
    if tag_context:
        parts.append(tag_context)
    file_context = attachment_context(attachments or [])
    if file_context:
        parts.append(file_context)
    if current_file:
        parts.append(f"Текущий файл в интерфейсе: {current_file}")
    return "\n\n".join(part for part in parts if part)


def backup_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = workspace_root(required=False)
    if root is None:
        return None
    try:
        rel_path = path.relative_to(root)
    except ValueError:
        safe_parts = [part.replace(":", "") for part in path.parts if part not in {path.anchor, "\\", "/"}]
        rel_path = Path("external", *safe_parts)
    backup_path = root / ".codex-lite" / "backups" / stamp / rel_path
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)
    return to_rel(backup_path)


def write_text_file(path: Path, content: str, create_dirs: bool = True) -> dict[str, Any]:
    if path.exists() and path.is_dir():
        raise AppError(f"Cannot write over a directory: {to_rel(path)}", 400)
    if create_dirs:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.parent.exists():
        raise AppError(f"Parent directory does not exist: {to_rel(path.parent)}", 400)
    backup = backup_file(path)
    path.write_text(content, encoding="utf-8", newline="")
    return {
        "path": to_rel(path),
        "bytes": path.stat().st_size,
        "backup": backup,
        "summary": f"Wrote {to_rel(path)} ({path.stat().st_size} bytes)",
    }


def chat_file_path_for(root: Path, chat_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9-]{32,36}", chat_id or ""):
        raise AppError("Invalid chat id", 400)
    return chat_dir_for(root) / f"{chat_id}.json"


def chat_file_path(chat_id: str) -> Path:
    root = workspace_root(required=False)
    if root:
        return chat_file_path_for(root, chat_id)
    if not re.fullmatch(r"[a-f0-9-]{32,36}", chat_id or ""):
        raise AppError("Invalid chat id", 400)
    return general_chat_dir() / f"{chat_id}.json"


def chat_title_from_message(message: str) -> str:
    title = re.sub(r"\s+", " ", message.strip()).strip()
    if not title:
        return "Новый чат"
    return title[:64].rstrip() + ("..." if len(title) > 64 else "")


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AppError("Chat not found", 404) from exc
    except json.JSONDecodeError as exc:
        raise AppError(f"Chat file is corrupted: {path.name}", 500) from exc


def save_chat_record(record: dict[str, Any]) -> None:
    chat_dir().mkdir(parents=True, exist_ok=True)
    path = chat_file_path(str(record["id"]))
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def create_chat_record(title: str | None = None) -> dict[str, Any]:
    timestamp = now_iso()
    record = {
        "id": str(uuid.uuid4()),
        "title": (title or "Новый чат").strip() or "Новый чат",
        "created_at": timestamp,
        "updated_at": timestamp,
        "model": DEFAULT_MODEL,
        "messages": [],
        "llm_messages": [{"role": "system", "content": system_prompt()}],
    }
    save_chat_record(record)
    return record


def load_chat_record(chat_id: str) -> dict[str, Any]:
    record = read_json_file(chat_file_path(chat_id))
    if "messages" not in record or not isinstance(record["messages"], list):
        record["messages"] = []
    if "llm_messages" not in record or not isinstance(record["llm_messages"], list):
        record["llm_messages"] = [{"role": "system", "content": system_prompt()}]
    return record


def list_chat_records_for(root: Path) -> list[dict[str, Any]]:
    return list_chat_records_in_dir(chat_dir_for(root))


def list_chat_records_in_dir(directory: Path) -> list[dict[str, Any]]:
    chats: list[dict[str, Any]] = []
    if not directory.exists():
        return chats
    for path in directory.glob("*.json"):
        try:
            record = read_json_file(path)
        except AppError:
            continue
        messages = record.get("messages") if isinstance(record.get("messages"), list) else []
        chats.append(
            {
                "id": record.get("id") or path.stem,
                "title": record.get("title") or "Новый чат",
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
                "model": record.get("model") or DEFAULT_MODEL,
                "message_count": len(messages),
            }
        )
    return sorted(chats, key=lambda item: item.get("updated_at") or "", reverse=True)


def list_chat_records() -> list[dict[str, Any]]:
    root = workspace_root(required=False)
    return list_chat_records_for(root) if root else list_chat_records_in_dir(general_chat_dir())


def list_projects_with_chats() -> list[dict[str, Any]]:
    current = project_summary()
    current_key = project_path_key(current["path"])
    projects: list[dict[str, Any]] = []
    seen: set[str] = set()
    general_project = general_project_summary()
    projects.append(general_project)
    seen.add("")
    for item in load_recent_projects():
        path_value = item.get("path")
        if not path_value:
            continue
        try:
            root = Path(path_value).expanduser().resolve()
        except OSError:
            continue
        key = project_path_key(root)
        if key in seen:
            continue
        seen.add(key)
        is_current = bool(current_key and key == current_key)
        summary = {**project_summary(root), "last_opened": item.get("last_opened"), "current": is_current}
        summary["chats"] = list_chat_records() if is_current else (list_chat_records_for(root) if summary["exists"] else [])
        projects.append(summary)
    if current_key and current_key not in seen:
        projects.append({**current, "current": True, "chats": list_chat_records()})
    return projects[:20]


def public_chat_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "title": record.get("title") or "Новый чат",
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "model": record.get("model") or DEFAULT_MODEL,
        "messages": record.get("messages") if isinstance(record.get("messages"), list) else [],
    }


def delete_chat_record(chat_id: str, root: Path | None = None, general: bool = False) -> dict[str, Any]:
    if general:
        if not re.fullmatch(r"[a-f0-9-]{32,36}", chat_id or ""):
            raise AppError("Invalid chat id", 400)
        path = general_chat_dir() / f"{chat_id}.json"
    elif root:
        path = chat_file_path_for(root.resolve(), chat_id)
    else:
        path = chat_file_path(chat_id)
    if not path.exists():
        raise AppError("Chat not found", 404)
    path.unlink()
    return {"ok": True, "id": chat_id}


def list_files_tool(path: str = ".", depth: int = 2, include_hidden: bool = False) -> dict[str, Any]:
    base = resolve_user_path(path)
    if not base.exists():
        raise AppError(f"Path not found: {path}", 404)
    depth = max(0, min(int(depth or 0), 6))
    entries: list[dict[str, Any]] = []

    def visit(directory: Path, remaining: int) -> None:
        if len(entries) >= 600:
            return
        try:
            children = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except OSError as exc:
            entries.append({"path": to_rel(directory), "type": "error", "error": str(exc)})
            return
        for child in children:
            if len(entries) >= 600:
                break
            if not include_hidden and child.name.startswith("."):
                continue
            if child.is_dir() and is_skipped_dir(child):
                continue
            try:
                stat = child.stat()
                item = {
                    "path": to_rel(child),
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": 0 if child.is_dir() else stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                }
                entries.append(item)
                if child.is_dir() and remaining > 1:
                    visit(child, remaining - 1)
            except OSError as exc:
                entries.append({"path": to_rel(child), "type": "error", "error": str(exc)})

    if base.is_file():
        stat = base.stat()
        entries.append(
            {
                "path": to_rel(base),
                "name": base.name,
                "type": "file",
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            }
        )
    else:
        visit(base, depth)

    return {
        "root": to_rel(base),
        "entries": entries,
        "truncated": len(entries) >= 600,
        "summary": f"Listed {len(entries)} entries under {to_rel(base)}",
    }


def read_file_tool(path: str, start_line: int = 1, max_lines: int = 400) -> dict[str, Any]:
    target = resolve_user_path(path)
    text = read_text_file(target)
    lines = text.splitlines()
    start = max(1, int(start_line or 1))
    max_lines = max(1, min(int(max_lines or 400), 1200))
    selected = lines[start - 1 : start - 1 + max_lines]
    end = start + len(selected) - 1 if selected else start
    return {
        "path": to_rel(target),
        "start_line": start,
        "end_line": end,
        "total_lines": len(lines),
        "truncated": end < len(lines),
        "content": "\n".join(f"{start + index}: {line}" for index, line in enumerate(selected)),
        "summary": f"Read {to_rel(target)} lines {start}-{end}",
    }


def search_files_tool(query: str, path: str = ".", glob: str = "*", max_results: int = 80) -> dict[str, Any]:
    if not query:
        raise AppError("Search query is required", 400)
    base = resolve_user_path(path)
    if not base.exists():
        raise AppError(f"Search path not found: {path}", 404)
    max_results = max(1, min(int(max_results or 80), 200))
    needle = query.lower()
    pattern = glob or "*"
    results: list[dict[str, Any]] = []

    def candidate_files() -> Any:
        if base.is_file():
            yield base
            return
        for root, dirs, files in os.walk(base):
            root_path = Path(root)
            dirs[:] = [name for name in dirs if name not in IGNORED_DIRS and not name.startswith(".")]
            for filename in files:
                child = root_path / filename
                rel = to_rel(child)
                if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(rel, pattern):
                    yield child

    for file_path in candidate_files():
        if len(results) >= max_results:
            break
        try:
            if file_path.stat().st_size > MAX_FILE_BYTES or looks_binary(file_path):
                continue
            with file_path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if needle in line.lower():
                        results.append(
                            {
                                "path": to_rel(file_path),
                                "line": line_number,
                                "preview": line.strip()[:240],
                            }
                        )
                        if len(results) >= max_results:
                            break
        except OSError:
            continue

    return {
        "query": query,
        "path": to_rel(base),
        "results": results,
        "truncated": len(results) >= max_results,
        "summary": f"Found {len(results)} matches for {query!r}",
    }


def write_file_tool(path: str, content: str, create_dirs: bool = True) -> dict[str, Any]:
    if content is None:
        raise AppError("content is required", 400)
    target = resolve_user_path(path)
    return write_text_file(target, str(content), bool(create_dirs))


def patch_file_tool(path: str, find: str, replace: str, count: int = 1) -> dict[str, Any]:
    if not find:
        raise AppError("find must be a non-empty string", 400)
    target = resolve_user_path(path)
    original = read_text_file(target)
    if find not in original:
        raise AppError(f"Text to replace was not found in {to_rel(target)}", 404)
    count = int(count or 1)
    new_text = original.replace(find, replace or "", count if count > 0 else -1)
    changed = original != new_text
    result = write_text_file(target, new_text, create_dirs=False) if changed else {"path": to_rel(target)}
    result["summary"] = f"Patched {to_rel(target)}"
    return result


def web_search_tool(query: str, num_results: int = 8, gl: str = "us", hl: str = "ru") -> dict[str, Any]:
    if not query or not query.strip():
        raise AppError("query is required", 400)
    if not SEARCH_API_KEY:
        raise AppError("Ключ поиска в интернете не настроен.", 503)
    num_results = max(1, min(int(num_results or 8), 20))
    payload = {
        "q": query.strip(),
        "num": num_results,
        "gl": (gl or "us")[:8],
        "hl": (hl or "ru")[:8],
    }
    request = urllib.request.Request(SEARCH_API_URL, data=json_dumps(payload), method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("X-API-KEY", SEARCH_API_KEY)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AppError(f"Ошибка сервиса поиска HTTP {exc.code}: {detail}", 502) from exc
    except urllib.error.URLError as exc:
        raise AppError(f"Сервис поиска недоступен: {exc}", 502) from exc
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise AppError("Сервис поиска вернул некорректный JSON", 502) from exc

    organic = []
    for item in data.get("organic") or []:
        if not isinstance(item, dict):
            continue
        organic.append(
            {
                "title": item.get("title"),
                "link": item.get("link"),
                "snippet": item.get("snippet"),
                "date": item.get("date"),
                "position": item.get("position"),
            }
        )

    news = []
    for item in data.get("news") or []:
        if not isinstance(item, dict):
            continue
        news.append(
            {
                "title": item.get("title"),
                "link": item.get("link"),
                "snippet": item.get("snippet"),
                "date": item.get("date"),
                "source": item.get("source"),
            }
        )

    return {
        "query": query.strip(),
        "results": organic[:num_results],
        "news": news[:num_results],
        "answer_box": data.get("answerBox"),
        "knowledge_graph": data.get("knowledgeGraph"),
        "people_also_ask": data.get("peopleAlsoAsk"),
        "summary": f"Found {len(organic)} web results for {query.strip()!r}",
    }


def ollama_model_names() -> set[str]:
    data = ollama_request_json("/api/tags", timeout=15)
    return {str(item.get("name") or item.get("model") or "") for item in data.get("models", []) if isinstance(item, dict)}


def ensure_ollama_model(model: str) -> bool:
    if model in ollama_model_names():
        return False
    ollama_request_json("/api/pull", payload={"name": model, "stream": False}, timeout=86400)
    return True


def looks_like_gpu_memory_error(message: str) -> bool:
    text = message.lower()
    return any(token in text for token in ["out of memory", "vram", "cuda", "gpu", "memory allocation", "unable to allocate"])


def read_image_base64(path: Path) -> str:
    if not path.exists():
        raise AppError(f"Image not found: {to_rel(path)}", 404)
    if not path.is_file():
        raise AppError(f"Not an image file: {to_rel(path)}", 400)
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise AppError(f"Unsupported image type: {path.suffix or path.name}", 415)
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise AppError(f"Image is too large ({size} bytes, limit {MAX_IMAGE_BYTES})", 413)
    return base64.b64encode(path.read_bytes()).decode("ascii")


def vision_chat(image_b64: str, prompt: str, model: str, force_cpu: bool = False) -> dict[str, Any]:
    options: dict[str, Any] = {"temperature": 0.1}
    if force_cpu:
        options["num_gpu"] = 0
    payload = {
        "model": model,
        "stream": False,
        "options": options,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Ответь на русском языке. Английский используй только для точного текста, "
                    "который видишь на изображении, кода, команд и названий.\n\n"
                    f"{prompt}"
                ),
                "images": [image_b64],
            }
        ],
    }
    return ollama_request_json("/api/chat", payload=payload, timeout=900)


def analyze_image_tool(path: str, prompt: str = "", model: str = "") -> dict[str, Any]:
    target = resolve_user_path(path)
    model_name = (model or VISION_MODEL).strip() or VISION_MODEL
    question = (prompt or "Опиши изображение подробно и точно. Если на изображении есть текст, перепиши его. Отвечай на русском языке.").strip()
    image_b64 = read_image_base64(target)
    downloaded = ensure_ollama_model(model_name)
    used_cpu = VISION_FORCE_CPU
    try:
        response = vision_chat(image_b64, question, model_name, force_cpu=VISION_FORCE_CPU)
    except AppError as exc:
        if VISION_FORCE_CPU or not looks_like_gpu_memory_error(str(exc)):
            raise
        used_cpu = True
        response = vision_chat(image_b64, question, model_name, force_cpu=True)
    message = response.get("message") if isinstance(response.get("message"), dict) else {}
    answer = str(message.get("content") or response.get("response") or "").strip()
    return {
        "path": to_rel(target),
        "model": model_name,
        "cpu": used_cpu,
        "downloaded": downloaded,
        "analysis": answer,
        "summary": f"Analyzed image {to_rel(target)} with {model_name}{' on CPU' if used_cpu else ''}",
    }


def run_command_tool(command: str, timeout_seconds: int = 30, cwd: str | None = None) -> dict[str, Any]:
    if not (ALLOW_COMMANDS or has_full_access()):
        raise AppError("Command execution is disabled. Enable Full access to allow shell commands.", 403)
    if not has_workspace() and not has_full_access():
        raise AppError("Проект не выбран, поэтому команды недоступны без Full Access.", 409)
    if not command or not command.strip():
        raise AppError("command is required", 400)
    timeout_seconds = max(1, min(int(timeout_seconds or 30), 300 if has_full_access() else 120))
    command_cwd = resolve_user_path(cwd or ".", allow_full_access=has_full_access())
    if not command_cwd.exists() or not command_cwd.is_dir():
        raise AppError(f"Command cwd is not a directory: {to_rel(command_cwd)}", 400)
    started = time.time()
    try:
        proc = subprocess.run(
            command,
            cwd=str(command_cwd),
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
        elapsed = round(time.time() - started, 2)
        return {
            "command": command,
            "cwd": to_rel(command_cwd),
            "exit_code": proc.returncode,
            "elapsed_seconds": elapsed,
            "stdout": truncate(proc.stdout, 30000),
            "stderr": truncate(proc.stderr, 30000),
            "summary": f"Ran command with exit code {proc.returncode} in {elapsed}s",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "cwd": to_rel(command_cwd),
            "timeout_seconds": timeout_seconds,
            "stdout": truncate(exc.stdout or "", 30000),
            "stderr": truncate(exc.stderr or "", 30000),
            "summary": f"Command timed out after {timeout_seconds}s",
        }


TOOL_FUNCTIONS = {
    "web_search": lambda args: web_search_tool(**args),
    "analyze_image": lambda args: analyze_image_tool(**args),
    "list_files": lambda args: list_files_tool(**args),
    "read_file": lambda args: read_file_tool(**args),
    "search_files": lambda args: search_files_tool(**args),
    "write_file": lambda args: write_file_tool(**args),
    "patch_file": lambda args: patch_file_tool(**args),
    "run_command": lambda args: run_command_tool(**args),
}


def tool_schemas() -> list[dict[str, Any]]:
    schemas: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the internet through the configured search provider for current or external information. Use this for news, fresh facts, library/product info, docs discovery, or anything outside the local project. Return links in the final answer when you use it.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "num_results": {"type": "integer", "description": "1 to 20 results."},
                        "gl": {"type": "string", "description": "Google country code, for example us, ru, de."},
                        "hl": {"type": "string", "description": "Interface language, for example ru or en."},
                    },
                    "required": ["query"],
                },
            },
        }
    ]
    if has_workspace() or has_full_access():
        schemas.extend([
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files and folders inside the local workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path inside the workspace."},
                        "depth": {"type": "integer", "description": "Directory recursion depth from 0 to 6."},
                        "include_hidden": {"type": "boolean", "description": "Include dotfiles and dotfolders."},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "analyze_image",
                "description": "Analyze a local image file with an Ollama vision model. Use when the user asks about a screenshot, photo, diagram, UI mockup, chart, or image file in the project. If GPU memory is insufficient, the server retries on CPU.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Image path in the workspace, or an absolute path when Full Access is enabled."},
                        "prompt": {"type": "string", "description": "What to inspect or answer about the image. Ask in Russian unless the user requested another language."},
                        "model": {"type": "string", "description": "Optional Ollama vision model override."},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a text file from the workspace with line numbers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "max_lines": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_files",
                "description": "Search text files in the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string"},
                        "glob": {"type": "string", "description": "Filename or relative path glob, for example *.py."},
                        "max_results": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Create or overwrite a UTF-8 text file in the workspace. Use only when the user asked for edits.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "create_dirs": {"type": "boolean"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "patch_file",
                "description": "Replace exact text inside one UTF-8 file. Prefer this over write_file for small edits.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "find": {"type": "string"},
                        "replace": {"type": "string"},
                        "count": {"type": "integer", "description": "Number of replacements. Use -1 for all."},
                    },
                    "required": ["path", "find", "replace"],
                },
            },
        },
        ])
    if ALLOW_COMMANDS or has_full_access():
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "description": "Run a shell command in the workspace. Use only when needed for verification or explicit user requests.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                            "timeout_seconds": {"type": "integer"},
                            "cwd": {"type": "string", "description": "Working directory. Full access may use paths outside the project."},
                        },
                        "required": ["command"],
                    },
                },
            }
        )
    return schemas


def legacy_system_prompt() -> str:
    root = workspace_root(required=False)
    workspace_line = str(root) if root else "Проект не выбран"
    file_note = (
        "A project folder is selected. You may use the provided file tools when needed."
        if root
        else "No project folder is selected. Answer as a normal assistant and do not try to inspect local project files unless the user opens a folder or enables Full Access."
    )
    command_note = (
        "Full access is enabled. You may inspect files anywhere on this PC and use run_command when needed. Be careful and keep destructive actions tied to the user's request."
        if has_full_access()
        else (
            "The run_command tool is available, but use it sparingly and only inside the workspace."
            if ALLOW_COMMANDS and root
            else "Shell command execution is disabled in this session."
        )
    )
    access_note = (
        "Full access mode: paths may be inside or outside the project."
        if has_full_access()
        else ("Sandbox mode: paths must stay inside the project workspace." if root else "No-project mode: local file tools are unavailable.")
    )
    return f"""Serplex is an autonomous coding assistant running as a local web app.

Workspace root: {workspace_line}
Model endpoint: {OLLAMA_BASE_URL}

You can inspect and edit files only through the provided tools. Never invent file contents; list/search/read first when context is needed.
{file_note}
Keep edits focused. Prefer patch_file for surgical edits and write_file for new files or complete rewrites.
Use native tool calls when you need tools. Do not print JSON tool-call objects as the final answer.
Before modifying files, make sure the user's request calls for a modification. Explain what changed and mention files touched.
{command_note}
{access_note}
Always answer in Russian by default, including long and complex coding tasks.
Do not switch to English just because the task is technical or large.
Use English only for exact code identifiers, file paths, commands, API names, library/framework names, model names, and established technical terms where translation would be unnatural.
Minimize англицизмы in normal prose: prefer clear Russian wording for explanations, status, summaries, questions, and UI/UX feedback.
If the user explicitly asks for another language, follow that explicit request.
"""


def system_prompt() -> str:
    root = workspace_root(required=False)
    workspace_line = str(root) if root else "Проект не выбран"
    file_note = (
        "Папка проекта выбрана. Когда нужно, используй файловые инструменты для чтения, поиска и правки файлов."
        if root
        else "Папка проекта не выбрана. Ответ формируется без чтения локальных файлов, пока не выбрана папка или не включён Full Access."
    )
    command_note = (
        "Full Access включён. Можно смотреть файлы в любом месте этого ПК и использовать run_command, когда это действительно нужно. Опасные действия допустимы только при прямом соответствии запросу пользователя."
        if has_full_access()
        else (
            "Инструмент run_command доступен, но применяется экономно и только внутри выбранной папки проекта."
            if ALLOW_COMMANDS and root
            else "Запуск shell-команд в этой сессии отключён."
        )
    )
    access_note = (
        "Режим доступа: Full Access, пути могут быть внутри проекта или за его пределами."
        if has_full_access()
        else ("Режим доступа: песочница, пути должны оставаться внутри выбранной папки проекта." if root else "Режим без проекта: локальные файловые инструменты недоступны.")
    )
    return f"""Serplex — автономный ассистент для программирования, запущенный как локальное веб-приложение.

Корень проекта: {workspace_line}
Endpoint модели: {OLLAMA_BASE_URL}

Работа с файлами выполняется только через предоставленные инструменты. Содержимое файлов нельзя выдумывать: если нужен контекст, сначала используются list/search/read.
{file_note}
Правки выполняются точечно. Для небольших изменений предпочтителен patch_file, для новых файлов или полной перезаписи используется write_file.
Если нужны инструменты, вызываются tool calls. JSON tool-call не печатается как финальный ответ.
Перед изменением файлов нужна проверка, что запрос пользователя действительно подразумевает изменение. В ответе указывается, что изменилось, и перечисляются затронутые файлы.
{command_note}
{access_note}

Язык ответа:
- Ответ пользователю всегда на русском языке, включая длинные и сложные задачи по коду.
- Английский не используется только из-за технического или объёмного характера задачи.
- Английский допускается только для точных идентификаторов кода, путей, команд, API, библиотек, framework names, model names и устоявшихся технических терминов, где перевод будет неестественным.
- В обычных объяснениях, статусах, выводах, вопросах и UX/UI-заметках используется нормальный русский с минимумом англицизмов.
- Если явно запрошен другой язык, используется именно он.
"""


def system_prompt() -> str:
    root = workspace_root(required=False)
    workspace_line = str(root) if root else "Проект не выбран"
    file_note = (
        "Папка проекта выбрана. Используй файловые инструменты, когда нужно прочитать, найти или изменить файлы."
        if root
        else "Папка проекта не выбрана. Ответ формируется без чтения локальных файлов, пока не выбрана папка или не включён Full Access."
    )
    command_note = (
        "Full Access включён: можно смотреть файлы вне проекта и использовать run_command, если это действительно нужно."
        if has_full_access()
        else ("Песочница включена: пути должны оставаться внутри выбранной папки проекта." if root else "Локальные файловые инструменты недоступны без проекта.")
    )
    return f"""Serplex — автономный ассистент для программирования в локальном веб-приложении.

Корень проекта: {workspace_line}
Endpoint модели: {OLLAMA_BASE_URL}

Инструменты:
- list_files, read_file, search_files, write_file, patch_file работают с локальными файлами. Содержимое файлов не выдумывается: если нужен контекст, сначала используется чтение или поиск инструментами.
- web_search ищет в интернете через настроенный поисковый сервис. Используется для свежих фактов, новостей, внешней документации, версий библиотек, продуктов и всего, что может измениться. Если использован web_search, в финальном ответе нужны ссылки.
- analyze_image анализирует локальные изображения через vision-модель {VISION_MODEL}. Используется для скриншотов, фото, схем, UI-макетов, графиков и изображений в проекте. Если GPU-памяти не хватает, сервер повторит анализ на CPU.

{file_note}
{command_note}

Правила работы:
- Правки выполняются точечно. Для небольших изменений предпочтителен patch_file, для новых файлов или полной перезаписи используется write_file.
- Если нужны инструменты, вызываются tool calls. JSON tool-call не печатается как финальный ответ.
- Перед изменением файлов нужна проверка, что запрос пользователя действительно подразумевает изменение.
- После правок кратко указывается, что изменилось, и перечисляются затронутые файлы.

Язык ответа:
- Ответ пользователю всегда на русском языке, включая длинные и сложные задачи по коду.
- Английский не используется только из-за технического или объёмного характера задачи.
- Английский допускается только для точных идентификаторов кода, путей, команд, API, названий библиотек, framework names, model names и устоявшихся технических терминов.
- В обычных объяснениях, статусах, выводах, вопросах и UX/UI-заметках используется нормальный русский с минимумом англицизмов.
- Если явно запрошен другой язык, используется именно он.
"""


def ollama_request_json(path: str, payload: dict[str, Any] | None = None, timeout: int = 600) -> dict[str, Any]:
    require_model_api_key()
    url = ollama_url(path)
    body = None if payload is None else json_dumps(payload)
    request = urllib.request.Request(url, data=body, method="GET" if payload is None else "POST")
    request.add_header("Content-Type", "application/json")
    add_ollama_headers(request)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise_ollama_http_error(exc)
    except urllib.error.URLError as exc:
        if try_recover_ollama_endpoint():
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = response.read()
            except urllib.error.HTTPError as retry_exc:
                raise_ollama_http_error(retry_exc)
            except urllib.error.URLError as retry_exc:
                raise AppError(f"Ollama endpoint is not reachable at {OLLAMA_BASE_URL}: {retry_exc}", 502) from retry_exc
        else:
            hint = ""
            if can_auto_start_tunnel():
                hint = " Auto-reconnect was attempted; check logs/local-codex-auto-tunnel.err.log."
            else:
                hint = " Start Ollama or the SSH tunnel, then retry."
            raise AppError(f"Ollama endpoint is not reachable at {OLLAMA_BASE_URL}: {exc}.{hint}", 502) from exc
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise AppError(f"Ollama returned invalid JSON: {raw[:300]!r}", 502) from exc


def ollama_chat(messages: list[dict[str, Any]], model: str, temperature: float = 0.15) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": messages,
        "tools": tool_schemas(),
        "tool_choice": "auto",
        "temperature": temperature,
        "stream": False,
    }
    return ollama_request_json("/v1/chat/completions", payload=payload, timeout=900)


def ollama_chat_no_tools(messages: list[dict[str, Any]], model: str, temperature: float = 0.15) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    return ollama_request_json("/v1/chat/completions", payload=payload, timeout=900)


def ollama_chat_stream_step(messages: list[dict[str, Any]], model: str, emit: Any, temperature: float = 0.15) -> tuple[str, list[dict[str, Any]]]:
    require_model_api_key()
    payload = {
        "model": model,
        "messages": messages,
        "tools": tool_schemas(),
        "tool_choice": "auto",
        "temperature": temperature,
        "stream": True,
    }
    url = ollama_url("/v1/chat/completions")
    request = urllib.request.Request(url, data=json_dumps(payload), method="POST")
    request.add_header("Content-Type", "application/json")
    add_ollama_headers(request)

    content_parts: list[str] = []
    tool_calls_by_index: dict[int, dict[str, Any]] = {}
    emitted_tool_delta = False

    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                token = delta.get("content") or ""
                if token:
                    content_parts.append(token)
                    emit("token", {"content": token})
                for raw_call in delta.get("tool_calls") or []:
                    if not emitted_tool_delta:
                        emit("tool_delta", {})
                        emitted_tool_delta = True
                    index = int(raw_call.get("index") or 0)
                    current = tool_calls_by_index.setdefault(
                        index,
                        {
                            "id": raw_call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    if raw_call.get("id"):
                        current["id"] = raw_call["id"]
                    function = raw_call.get("function") or {}
                    if function.get("name"):
                        current["function"]["name"] += function["name"]
                    if function.get("arguments"):
                        current["function"]["arguments"] += function["arguments"]
    except urllib.error.HTTPError as exc:
        raise_ollama_http_error(exc)
    except urllib.error.URLError as exc:
        if try_recover_ollama_endpoint():
            return ollama_chat_stream_step(messages, model, emit, temperature)
        raise AppError(f"Ollama endpoint is not reachable at {OLLAMA_BASE_URL}: {exc}. Auto-reconnect was attempted; check logs/local-codex-auto-tunnel.err.log.", 502) from exc

    content = "".join(content_parts)
    tool_calls = [
        call
        for _, call in sorted(tool_calls_by_index.items())
        if call.get("function", {}).get("name")
    ]
    return content, tool_calls


def ollama_chat_no_tools_stream(messages: list[dict[str, Any]], model: str, emit: Any, temperature: float = 0.15) -> str:
    require_model_api_key()
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    url = ollama_url("/v1/chat/completions")
    request = urllib.request.Request(url, data=json_dumps(payload), method="POST")
    request.add_header("Content-Type", "application/json")
    add_ollama_headers(request)

    content_parts: list[str] = []
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                token = delta.get("content") or ""
                if token:
                    content_parts.append(token)
                    emit("token", {"content": token})
    except urllib.error.HTTPError as exc:
        raise_ollama_http_error(exc)
    except urllib.error.URLError as exc:
        if try_recover_ollama_endpoint():
            return ollama_chat_no_tools_stream(messages, model, emit, temperature)
        raise AppError(f"Ollama endpoint is not reachable at {OLLAMA_BASE_URL}: {exc}. Auto-reconnect was attempted; check logs/local-codex-auto-tunnel.err.log.", 502) from exc

    return "".join(content_parts)


def normalize_tool_arguments(raw_args: Any) -> dict[str, Any]:
    if raw_args is None or raw_args == "":
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def extract_text_tool_calls(content: str) -> list[dict[str, Any]]:
    if not content:
        return []

    candidates: list[str] = [content.strip()]
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", content, flags=re.IGNORECASE | re.DOTALL):
        candidates.append(match.group(1).strip())
    for match in re.finditer(r"<tool_call>\s*(.*?)\s*</tool_call>", content, flags=re.IGNORECASE | re.DOTALL):
        candidates.append(match.group(1).strip())

    calls: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_call(item: Any) -> None:
        if not isinstance(item, dict):
            return
        name = item.get("name") or item.get("tool") or item.get("function")
        arguments = item.get("arguments") or item.get("args") or {}
        if isinstance(name, dict):
            arguments = name.get("arguments") or arguments
            name = name.get("name")
        if not isinstance(name, str) or name not in TOOL_FUNCTIONS:
            return
        args = normalize_tool_arguments(arguments)
        key = json.dumps({"name": name, "arguments": args}, sort_keys=True, ensure_ascii=False)
        if key in seen:
            return
        seen.add(key)
        calls.append(
            {
                "id": f"text_call_{uuid.uuid4().hex[:10]}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            }
        )

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            for item in parsed:
                add_call(item)
        else:
            add_call(parsed)

    return calls


def execute_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    try:
        if name not in TOOL_FUNCTIONS:
            raise AppError(f"Unknown tool: {name}", 400)
        result = TOOL_FUNCTIONS[name](args)
        return {
            "ok": True,
            "tool": name,
            "args": args,
            "elapsed_seconds": round(time.time() - started, 2),
            "result": result,
            "summary": result.get("summary") if isinstance(result, dict) else f"{name} completed",
        }
    except Exception as exc:
        status = exc.status if isinstance(exc, AppError) else 500
        return {
            "ok": False,
            "tool": name,
            "args": args,
            "status": status,
            "elapsed_seconds": round(time.time() - started, 2),
            "error": str(exc),
            "summary": f"{name} failed: {exc}",
        }


def trim_session(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(messages) <= MAX_SESSION_MESSAGES:
        return messages
    return [messages[0]] + messages[-(MAX_SESSION_MESSAGES - 1) :]


def llm_messages_from_visible(visible_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt()}]
    for item in visible_messages:
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            if role == "user":
                messages.append(
                    {
                        "role": role,
                        "content": build_user_content(
                            content,
                            normalize_task_tags(item.get("task_tags")),
                            normalize_attachments(item.get("attachments")),
                        ),
                    }
                )
            else:
                messages.append({"role": role, "content": content})
    return messages


def chat_agent(payload: dict[str, Any]) -> dict[str, Any]:
    apply_payload_workspace(payload)
    message = str(payload.get("message") or "").strip()
    if not message:
        raise AppError("message is required", 400)
    model = str(payload.get("model") or DEFAULT_MODEL)
    session_id = str(payload.get("session_id") or "")
    max_steps = max(1, min(int(payload.get("max_steps") or 8), 14))
    current_file = payload.get("current_file")
    task_tags = normalize_task_tags(payload.get("task_tags"))
    attachments = normalize_attachments(payload.get("attachments"))
    temperature = task_temperature(task_tags)

    chat = load_chat_record(session_id) if session_id else create_chat_record(chat_title_from_message(message))
    session_id = str(chat["id"])
    messages = chat.get("llm_messages") if isinstance(chat.get("llm_messages"), list) else []
    if not messages:
        messages = [{"role": "system", "content": system_prompt()}]
    if messages[0].get("role") == "system":
        messages[0]["content"] = system_prompt()
    else:
        messages.insert(0, {"role": "system", "content": system_prompt()})

    user_content = build_user_content(message, task_tags, attachments, current_file)
    messages.append({"role": "user", "content": user_content})
    chat.setdefault("messages", []).append({"role": "user", "content": message, "timestamp": now_iso(), "task_tags": task_tags, "attachments": attachments})

    visible_steps: list[dict[str, Any]] = []
    visible_thoughts: list[dict[str, str]] = []
    answer = ""

    for _ in range(max_steps):
        response = ollama_chat(messages, model, temperature)
        choices = response.get("choices") or []
        if not choices:
            raise AppError(f"Ollama returned no choices: {response}", 502)
        assistant = choices[0].get("message") or {}
        answer = assistant.get("content") or ""
        tool_calls = assistant.get("tool_calls") or []
        if not tool_calls:
            tool_calls = extract_text_tool_calls(answer)
        assistant_message: dict[str, Any] = {"role": "assistant", "content": answer}
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
            if answer.strip():
                visible_thoughts.append({"kind": "draft", "content": answer.strip()})
        messages.append(assistant_message)

        if not tool_calls:
            break

        for call in tool_calls:
            function = call.get("function") or {}
            tool_name = function.get("name")
            args = normalize_tool_arguments(function.get("arguments"))
            result = execute_tool(tool_name, args)
            visible_steps.append(
                {
                    "tool": tool_name,
                    "args": args,
                    "ok": result.get("ok"),
                    "summary": result.get("summary"),
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    "name": tool_name,
                    "content": truncate(json.dumps(result, ensure_ascii=False)),
                }
            )
    else:
        final_messages = trim_session(messages + [{"role": "user", "content": FINAL_ANSWER_PROMPT}])
        response = ollama_chat_no_tools(final_messages, model, temperature)
        choices = response.get("choices") or []
        if choices:
            answer = (choices[0].get("message") or {}).get("content") or ""
        answer = answer or "I could not produce a final answer from the collected tool context."
        messages = final_messages + [{"role": "assistant", "content": answer}]

    messages = trim_session(messages)
    timestamp = now_iso()
    chat["llm_messages"] = messages
    chat["model"] = model
    chat["updated_at"] = timestamp
    if not chat.get("title") or chat.get("title") == "Новый чат":
        chat["title"] = chat_title_from_message(message)
    chat.setdefault("messages", []).append(
        {
            "role": "assistant",
            "content": answer,
            "timestamp": timestamp,
            "model": model,
            "steps": visible_steps,
            "thoughts": visible_thoughts,
        }
    )
    save_chat_record(chat)
    return {
        "session_id": session_id,
        "chat": public_chat_record(chat),
        "model": model,
        "answer": answer,
        "steps": visible_steps,
        "workspace": str(workspace_root(required=False) or ""),
        "timestamp": now_iso(),
    }


def chat_agent_stream(payload: dict[str, Any], emit: Any) -> dict[str, Any]:
    apply_payload_workspace(payload)
    message = str(payload.get("message") or "").strip()
    if not message:
        raise AppError("message is required", 400)
    model = str(payload.get("model") or DEFAULT_MODEL)
    session_id = str(payload.get("session_id") or "")
    max_steps = max(1, min(int(payload.get("max_steps") or 8), 14))
    edit_index_value = payload.get("edit_message_index")
    task_tags = normalize_task_tags(payload.get("task_tags"))
    attachments = normalize_attachments(payload.get("attachments"))
    temperature = task_temperature(task_tags)
    if edit_index_value is not None and not session_id:
        raise AppError("session_id is required for editing a message", 400)

    chat = load_chat_record(session_id) if session_id else create_chat_record(chat_title_from_message(message))
    session_id = str(chat["id"])

    if edit_index_value is not None:
        visible = chat.get("messages") if isinstance(chat.get("messages"), list) else []
        try:
            edit_index = int(edit_index_value)
        except (TypeError, ValueError) as exc:
            raise AppError("edit_message_index must be a number", 400) from exc
        if edit_index < 0 or edit_index >= len(visible):
            raise AppError("Message to edit was not found", 404)
        if visible[edit_index].get("role") != "user":
            raise AppError("Only user messages can be edited", 400)
        chat["messages"] = visible[:edit_index]
        messages = llm_messages_from_visible(chat["messages"])
        if edit_index == 0:
            chat["title"] = chat_title_from_message(message)
    else:
        messages = chat.get("llm_messages") if isinstance(chat.get("llm_messages"), list) else []
        if not messages:
            messages = [{"role": "system", "content": system_prompt()}]
        if messages[0].get("role") == "system":
            messages[0]["content"] = system_prompt()
        else:
            messages.insert(0, {"role": "system", "content": system_prompt()})

    timestamp = now_iso()
    messages.append({"role": "user", "content": build_user_content(message, task_tags, attachments)})
    user_message = {"role": "user", "content": message, "timestamp": timestamp, "task_tags": task_tags, "attachments": attachments}
    if edit_index_value is not None:
        user_message["edited"] = True
    chat.setdefault("messages", []).append(user_message)
    chat["llm_messages"] = messages
    chat["model"] = model
    chat["updated_at"] = timestamp
    save_chat_record(chat)
    emit("chat", {"chat": public_chat_record(chat)})

    visible_steps: list[dict[str, Any]] = []
    visible_thoughts: list[dict[str, str]] = []
    answer = ""

    for _ in range(max_steps):
        emit("status", {"status": "thinking"})
        content, tool_calls = ollama_chat_stream_step(messages, model, emit, temperature)
        if tool_calls:
            emit("replace", {"content": ""})
        else:
            text_calls = extract_text_tool_calls(content)
            if text_calls:
                tool_calls = text_calls
                emit("replace", {"content": ""})

        assistant_message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
            if content.strip():
                visible_thoughts.append({"kind": "draft", "content": content.strip()})
        messages.append(assistant_message)

        if not tool_calls:
            answer = content
            break

        for call in tool_calls:
            function = call.get("function") or {}
            tool_name = function.get("name")
            args = normalize_tool_arguments(function.get("arguments"))
            emit("tool_start", {"tool": tool_name, "args": args})
            result = execute_tool(tool_name, args)
            step = {
                "tool": tool_name,
                "args": args,
                "ok": result.get("ok"),
                "summary": result.get("summary"),
            }
            visible_steps.append(step)
            emit("tool_end", step)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    "name": tool_name,
                    "content": truncate(json.dumps(result, ensure_ascii=False)),
                }
            )
    else:
        emit("replace", {"content": ""})
        final_messages = trim_session(messages + [{"role": "user", "content": FINAL_ANSWER_PROMPT}])
        answer = ollama_chat_no_tools_stream(final_messages, model, emit, temperature).strip()
        if not answer:
            answer = "Не смог собрать финальный ответ из уже полученного контекста."
            emit("token", {"content": answer})
        messages = final_messages + [{"role": "assistant", "content": answer}]

    messages = trim_session(messages)
    done_at = now_iso()
    chat["llm_messages"] = messages
    chat["model"] = model
    chat["updated_at"] = done_at
    if not chat.get("title") or chat.get("title") == "Новый чат":
        chat["title"] = chat_title_from_message(message)
    chat.setdefault("messages", []).append(
        {
            "role": "assistant",
            "content": answer,
            "timestamp": done_at,
            "model": model,
            "steps": visible_steps,
            "thoughts": visible_thoughts,
        }
    )
    save_chat_record(chat)
    result = {
        "session_id": session_id,
        "chat": public_chat_record(chat),
        "model": model,
        "answer": answer,
        "steps": visible_steps,
        "workspace": str(workspace_root(required=False) or ""),
        "timestamp": done_at,
    }
    emit("done", result)
    return result


def get_models() -> dict[str, Any]:
    data = ollama_request_json("/api/tags", timeout=10)
    models = []
    for item in data.get("models", []):
        models.append(
            {
                "name": item.get("name"),
                "size": item.get("size"),
                "modified_at": item.get("modified_at"),
            }
        )
    return {"models": models, "default_model": DEFAULT_MODEL}


def parse_float(value: str, default: float = 0.0) -> float:
    try:
        return float(str(value).strip().replace("%", ""))
    except (TypeError, ValueError):
        return default


def parse_optional_float(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or "N/A" in text.upper():
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text.replace(",", "."))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def parse_int(value: str, default: int = 0) -> int:
    try:
        return int(float(str(value).strip().replace("%", "")))
    except (TypeError, ValueError):
        return default


def run_remote_metrics_command(timeout: int = 12) -> tuple[str, str | None]:
    command = r"""sh -lc 'echo __GPU__; nvidia-smi --query-gpu=index,name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw.instant,power.draw.average,power.draw,power.limit,clocks.current.graphics,clocks.max.graphics,pstate --format=csv,noheader,nounits 2>/dev/null || true; echo __POWER__; nvidia-smi -q -d POWER 2>/dev/null || true; echo __LOAD__; cat /proc/loadavg 2>/dev/null || true; echo __CORES__; nproc 2>/dev/null || true; echo __MEM__; awk "/MemTotal:/ {t=\$2*1024} /MemAvailable:/ {a=\$2*1024} END {print t \",\" t-a \",\" a}" /proc/meminfo 2>/dev/null || true; echo __DISK__; df -B1 -P 2>/dev/null | tail -n +2 || true; echo __TEMP__; sensors 2>/dev/null | head -80 || true'"""
    plink = Path(r"C:\Program Files\PuTTY\plink.exe")
    if plink.exists() and not REMOTE_SSH_USE_CONFIG:
        args = [
            str(plink),
            "-batch",
            "-pw",
            REMOTE_SSH_PASSWORD,
            "-ssh",
            "-P",
            str(REMOTE_SSH_PORT),
            "-l",
            REMOTE_SSH_USER,
            "-hostkey",
            REMOTE_SSH_HOSTKEY,
            REMOTE_SSH_HOST,
            command,
        ]
    else:
        ssh = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "OpenSSH" / "ssh.exe"
        args = [
            str(ssh),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
        ]
        if REMOTE_SSH_USE_CONFIG:
            args += [REMOTE_SSH_HOST, command]
        else:
            args += ["-p", str(REMOTE_SSH_PORT), f"{REMOTE_SSH_USER}@{REMOTE_SSH_HOST}", command]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except Exception as exc:
        return "", str(exc)
    error = proc.stderr.strip() if proc.returncode != 0 else None
    return proc.stdout, error


def fetch_relay_remote_metrics(timeout: int = 12) -> tuple[str, str | None, str]:
    if not MODEL_API_KEY.strip():
        return "", "Нужен API-ключ сервера для dashboard-метрик.", urllib.parse.urlparse(OLLAMA_BASE_URL).hostname or "server"
    request = urllib.request.Request(ollama_url("/api/remote-metrics"), method="GET")
    add_ollama_headers(request)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return "", f"Relay metrics HTTP {exc.code}: {detail}", urllib.parse.urlparse(OLLAMA_BASE_URL).hostname or "server"
    except Exception as exc:
        return "", f"Relay metrics unavailable: {exc}", urllib.parse.urlparse(OLLAMA_BASE_URL).hostname or "server"
    if not isinstance(data, dict):
        return "", "Relay metrics returned invalid payload", urllib.parse.urlparse(OLLAMA_BASE_URL).hostname or "server"
    return str(data.get("output") or ""), str(data.get("error") or "") or None, str(data.get("host") or "serplex")


def collect_remote_metrics_output() -> tuple[str, str | None, str]:
    if model_endpoint_requires_api_key():
        return fetch_relay_remote_metrics()
    output, error = run_remote_metrics_command()
    return output, error, REMOTE_SSH_HOST


def split_metric_sections(output: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in output.splitlines():
        if line.startswith("__") and line.endswith("__"):
            current = line.strip("_").lower()
            sections[current] = []
        elif current:
            sections.setdefault(current, []).append(line)
    return sections


def parse_nvidia_power_samples(lines: list[str]) -> dict[int, dict[str, float]]:
    samples: dict[int, dict[str, float]] = {}
    current_index = -1
    in_gpu_power = False
    in_samples = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^GPU\s+[0-9A-Fa-f]{8}:", line):
            current_index += 1
            samples.setdefault(current_index, {})
            in_gpu_power = False
            in_samples = False
            continue
        if current_index < 0:
            continue
        if line == "GPU Power Readings":
            in_gpu_power = True
            in_samples = False
            continue
        if line == "Power Samples":
            in_gpu_power = False
            in_samples = True
            continue
        if line.endswith("Readings") or line.startswith("EDPp "):
            in_gpu_power = False
            in_samples = False
            continue
        if ":" not in line:
            continue
        label, value = [part.strip() for part in line.split(":", 1)]
        parsed = parse_optional_float(value)
        if parsed is None:
            continue
        if in_gpu_power and label == "Current Power Limit":
            samples.setdefault(current_index, {})["limit_w"] = parsed
        elif in_samples and label == "Duration":
            samples.setdefault(current_index, {})["sample_duration_seconds"] = parsed
        elif in_samples and label == "Number of Samples":
            samples.setdefault(current_index, {})["sample_count"] = parsed
        elif in_samples and label in {"Avg", "Max", "Min"}:
            samples.setdefault(current_index, {})[f"sample_{label.lower()}_w"] = parsed
    return samples


def parse_remote_metrics(output: str, error: str | None = None, host: str | None = None) -> dict[str, Any]:
    sections = split_metric_sections(output)
    power_samples = parse_nvidia_power_samples(sections.get("power", []))
    gpus: list[dict[str, Any]] = []
    for line in sections.get("gpu", []):
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 14:
            continue
        gpu_index = parse_int(parts[0])
        utilization = parse_float(parts[2])
        memory_utilization = parse_float(parts[3])
        memory_used = parse_int(parts[4])
        memory_total = parse_int(parts[5])
        instant_power = parse_optional_float(parts[7])
        average_power = parse_optional_float(parts[8])
        draw_power = parse_optional_float(parts[9])
        power_limit = parse_optional_float(parts[10])
        current_clock = parse_optional_float(parts[11])
        max_clock = parse_optional_float(parts[12])
        pstate = parts[13]
        sample = power_samples.get(gpu_index, {})
        if instant_power is not None:
            power_w = instant_power
            power_source = "instant"
            power_window_seconds = 0
        elif average_power is not None:
            power_w = average_power
            power_source = "1s_avg"
            power_window_seconds = 1
        elif draw_power is not None:
            power_w = draw_power
            power_source = "1s_avg"
            power_window_seconds = 1
        else:
            power_w = sample.get("sample_avg_w")
            power_source = "power_samples_avg" if power_w is not None else "unavailable"
            power_window_seconds = sample.get("sample_duration_seconds")
        gpus.append(
            {
                "index": gpu_index,
                "name": parts[1],
                "utilization_percent": utilization,
                "memory_utilization_percent": memory_utilization,
                "memory_used_mb": memory_used,
                "memory_total_mb": memory_total,
                "memory_percent": round((memory_used / memory_total) * 100, 1) if memory_total else 0,
                "temperature_c": parse_float(parts[6]),
                "power_w": round(power_w, 2) if power_w is not None else None,
                "power_limit_w": round((power_limit or sample.get("limit_w", 0.0)), 2),
                "power_source": power_source,
                "power_window_seconds": power_window_seconds,
                "power_sample_avg_w": sample.get("sample_avg_w"),
                "power_sample_min_w": sample.get("sample_min_w"),
                "power_sample_max_w": sample.get("sample_max_w"),
                "power_sample_duration_seconds": sample.get("sample_duration_seconds"),
                "power_sample_count": sample.get("sample_count"),
                "clock_graphics_mhz": current_clock,
                "clock_graphics_max_mhz": max_clock,
                "pstate": pstate,
            }
        )

    load_line = (sections.get("load") or [""])[0].split()
    cores = parse_int((sections.get("cores") or ["0"])[0], 0)
    load1 = parse_float(load_line[0] if load_line else "0")
    mem_parts = ((sections.get("mem") or ["0,0,0"])[0]).split(",")
    mem_total = parse_int(mem_parts[0] if len(mem_parts) > 0 else "0")
    mem_used = parse_int(mem_parts[1] if len(mem_parts) > 1 else "0")
    mem_available = parse_int(mem_parts[2] if len(mem_parts) > 2 else "0")

    disks: list[dict[str, Any]] = []
    pseudo_filesystems = {"tmpfs", "devtmpfs", "squashfs", "overlay"}
    pseudo_mount_prefixes = ("/run", "/dev", "/sys", "/proc")
    for line in sections.get("disk", []):
        parts = line.split()
        if len(parts) < 6:
            continue
        mount = " ".join(parts[5:])
        if parts[0] in pseudo_filesystems or mount.startswith(pseudo_mount_prefixes):
            continue
        size = parse_int(parts[1])
        used = parse_int(parts[2])
        disks.append(
            {
                "filesystem": parts[0],
                "size_bytes": size,
                "used_bytes": used,
                "available_bytes": parse_int(parts[3]),
                "used_percent": parse_float(parts[4]),
                "mount": mount,
            }
        )

    return {
        "ok": error is None,
        "error": error,
        "host": host or REMOTE_SSH_HOST,
        "gpus": gpus,
        "cpu": {
            "cores": cores,
            "load_1m": load1,
            "load_5m": parse_float(load_line[1] if len(load_line) > 1 else "0"),
            "load_15m": parse_float(load_line[2] if len(load_line) > 2 else "0"),
            "load_percent": round(min((load1 / cores) * 100, 999), 1) if cores else 0,
        },
        "memory": {
            "total_bytes": mem_total,
            "used_bytes": mem_used,
            "available_bytes": mem_available,
            "used_percent": round((mem_used / mem_total) * 100, 1) if mem_total else 0,
        },
        "disks": disks,
        "temperatures": sections.get("temp", [])[:80],
    }


def get_ollama_runtime() -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "base_url": OLLAMA_BASE_URL}
    try:
        version = ollama_request_json("/api/version", timeout=5)
        ps = ollama_request_json("/api/ps", timeout=8)
        result.update({"ok": True, "version": version.get("version"), "models": ps.get("models", [])})
    except Exception as exc:
        result["error"] = str(exc)
    return result


def build_dashboard_metrics() -> dict[str, Any]:
    output, error, host = collect_remote_metrics_output()
    return {
        "timestamp": now_iso(),
        "remote": parse_remote_metrics(output, error, host),
        "ollama": get_ollama_runtime(),
        "app": {
            "model": DEFAULT_MODEL,
            "full_access": has_full_access(),
        },
    }


def refresh_dashboard_cache() -> None:
    try:
        data = build_dashboard_metrics()
    except Exception as exc:
        data = {
            "timestamp": now_iso(),
            "remote": {"ok": False, "error": str(exc), "host": REMOTE_SSH_HOST, "gpus": [], "disks": [], "temperatures": []},
            "ollama": {"ok": False, "base_url": OLLAMA_BASE_URL, "error": str(exc)},
            "app": {"model": DEFAULT_MODEL, "full_access": has_full_access()},
        }
    with DASHBOARD_LOCK:
        DASHBOARD_CACHE["timestamp"] = time.time()
        DASHBOARD_CACHE["data"] = data
        DASHBOARD_CACHE["refreshing"] = False


def start_dashboard_refresh_locked() -> None:
    DASHBOARD_CACHE["refreshing"] = True
    thread = threading.Thread(target=refresh_dashboard_cache, daemon=True)
    thread.start()


def dashboard_payload_with_age(data: dict[str, Any], sampled_at: float) -> dict[str, Any]:
    payload = dict(data)
    payload["cache_age_seconds"] = max(0.0, round(time.time() - sampled_at, 2))
    return payload


def get_dashboard_metrics() -> dict[str, Any]:
    now = time.time()
    with DASHBOARD_LOCK:
        cached = DASHBOARD_CACHE.get("data")
        sampled_at = float(DASHBOARD_CACHE.get("timestamp") or 0)
        refreshing = bool(DASHBOARD_CACHE.get("refreshing"))
        if cached:
            if now - sampled_at >= 1.0 and not refreshing:
                start_dashboard_refresh_locked()
            return dashboard_payload_with_age(cached, sampled_at)
        if not refreshing:
            DASHBOARD_CACHE["refreshing"] = True

    try:
        data = build_dashboard_metrics()
    except Exception as exc:
        data = {
            "timestamp": now_iso(),
            "remote": {"ok": False, "error": str(exc), "host": REMOTE_SSH_HOST, "gpus": [], "disks": [], "temperatures": []},
            "ollama": {"ok": False, "base_url": OLLAMA_BASE_URL, "error": str(exc)},
            "app": {"model": DEFAULT_MODEL, "full_access": has_full_access()},
        }
    with DASHBOARD_LOCK:
        DASHBOARD_CACHE["timestamp"] = time.time()
        DASHBOARD_CACHE["data"] = data
        DASHBOARD_CACHE["refreshing"] = False
    return data


def get_health() -> dict[str, Any]:
    result = {
        "project_name": project_name(),
        "workspace": str(workspace_root(required=False) or ""),
        "project": project_summary(),
        "default_model": DEFAULT_MODEL,
        "vision_model": VISION_MODEL,
        "web_search_enabled": bool(SEARCH_API_KEY),
        "app_version": APP_VERSION,
        "update_manifest_url": UPDATE_MANIFEST_URL,
        "ollama_base_url": OLLAMA_BASE_URL,
        "model_api_key_configured": bool(MODEL_API_KEY),
        "requires_model_api_key": model_endpoint_requires_api_key(),
        "allow_commands": ALLOW_COMMANDS,
        "full_access": has_full_access(),
        "server_time": now_iso(),
        "ollama": {"ok": False},
    }
    try:
        version = ollama_request_json("/api/version", timeout=5)
        result["ollama"] = {"ok": True, "version": version.get("version")}
    except Exception as exc:
        result["ollama"] = {"ok": False, "error": str(exc)}
    return result


def public_settings() -> dict[str, Any]:
    return {
        "endpoint": OLLAMA_BASE_URL,
        "model_api_key_configured": bool(MODEL_API_KEY.strip()),
        "requires_model_api_key": model_endpoint_requires_api_key(),
        "web_search_enabled": bool(SEARCH_API_KEY),
        "update_manifest_url": UPDATE_MANIFEST_URL,
    }


def update_app_settings(payload: dict[str, Any]) -> dict[str, Any]:
    global MODEL_API_KEY
    model_api_key = str(payload.get("model_api_key") or "").strip()
    clear_model_api_key = bool(payload.get("clear_model_api_key"))
    with RUNTIME_SECRETS_LOCK:
        secrets_data = load_runtime_secrets()
        if clear_model_api_key:
            secrets_data.pop("model_api_key", None)
            MODEL_API_KEY = ""
        elif model_api_key:
            secrets_data["model_api_key"] = model_api_key
            MODEL_API_KEY = model_api_key
        save_runtime_secrets(secrets_data)
    return public_settings()


class Handler(BaseHTTPRequestHandler):
    server_version = "Serplex/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("[%s] %s\n" % (datetime.now().strftime("%H:%M:%S"), fmt % args))
        sys.stdout.flush()

    def prepare_request_context(self) -> None:
        set_current_user(self.headers.get(USER_HEADER) or self.headers.get(LEGACY_USER_HEADER))
        REQUEST_CONTEXT.workspace_override_set = False
        REQUEST_CONTEXT.workspace_override = None

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json_dumps(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message: str, status: int = 500, detail: str | None = None) -> None:
        payload = {"ok": False, "error": message}
        if detail:
            payload["detail"] = detail
        self.send_json(payload, status)

    def send_binary_file(self, path: Path, filename: str, size: int) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            with path.open("rb") as source:
                shutil.copyfileobj(source, self.wfile, length=1024 * 1024)
        finally:
            path.unlink(missing_ok=True)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length > MAX_BODY_BYTES:
            raise AppError(f"Request body is too large ({length} bytes)", 413)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise AppError(f"Invalid JSON body: {exc}", 400) from exc
        if not isinstance(data, dict):
            raise AppError("JSON body must be an object", 400)
        return data

    def stream_chat(self, payload: dict[str, Any]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(event: str, data: Any) -> None:
            packet = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
            try:
                self.wfile.write(packet.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
                raise ClientDisconnected() from exc

        try:
            chat_agent_stream(payload, emit)
        except ClientDisconnected:
            return
        except Exception as exc:
            status = exc.status if isinstance(exc, AppError) else 500
            try:
                emit("error", {"status": status, "error": str(exc)})
            except ClientDisconnected:
                return

    def do_GET(self) -> None:
        try:
            self.prepare_request_context()
            parsed = urllib.parse.urlparse(self.path)
            route = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            if route == "/api/config":
                self.send_json(
                    {
                        "project_name": project_name(),
                        "workspace": str(workspace_root(required=False) or ""),
                        "project": project_summary(),
                        "projects": list_projects_with_chats(),
                        "recent_projects": load_recent_projects(),
                        "default_model": DEFAULT_MODEL,
                        "vision_model": VISION_MODEL,
                        "web_search_enabled": bool(SEARCH_API_KEY),
                        "app_version": APP_VERSION,
                        "update_manifest_url": UPDATE_MANIFEST_URL,
                        "ollama_base_url": OLLAMA_BASE_URL,
                        "model_api_key_configured": bool(MODEL_API_KEY),
                        "requires_model_api_key": model_endpoint_requires_api_key(),
                        "allow_commands": ALLOW_COMMANDS,
                        "full_access": has_full_access(),
                        "chat_store": str(chat_dir()),
                        "user": {"id": current_user_id()},
                    }
                )
            elif route == "/api/health":
                self.send_json(get_health())
            elif route == "/api/settings":
                self.send_json(public_settings())
            elif route == "/api/dashboard":
                self.send_json(get_dashboard_metrics())
            elif route == "/api/update":
                self.send_json(update_status())
            elif route == "/api/update/download":
                path, filename, size = download_verified_update_file()
                self.send_binary_file(path, filename, size)
            elif route == "/api/models":
                self.send_json(get_models())
            elif route == "/api/access":
                self.send_json({"full_access": has_full_access(), "allow_commands": ALLOW_COMMANDS})
            elif route == "/api/projects":
                projects = list_projects_with_chats()
                self.send_json({"current": project_summary(), "projects": projects, "recent": projects})
            elif route == "/api/projects/browse":
                path = (query.get("path") or [""])[0]
                self.send_json(browse_folders(path))
            elif route == "/api/chats":
                self.send_json({"project_name": project_name(), "workspace": str(workspace_root(required=False) or ""), "chats": list_chat_records()})
            elif route.startswith("/api/chats/"):
                chat_id = route.rsplit("/", 1)[-1]
                self.send_json(public_chat_record(load_chat_record(chat_id)))
            elif route == "/api/tree":
                path = (query.get("path") or ["."])[0]
                depth = int((query.get("depth") or ["2"])[0])
                self.send_json(list_files_tool(path=path, depth=depth, include_hidden=False))
            elif route == "/api/file":
                path = (query.get("path") or [""])[0]
                target = resolve_user_path(path)
                content = read_text_file(target)
                self.send_json({"path": to_rel(target), "content": content, "size": target.stat().st_size})
            elif route == "/api/search":
                needle = (query.get("query") or [""])[0]
                path = (query.get("path") or ["."])[0]
                glob = (query.get("glob") or ["*"])[0]
                self.send_json(search_files_tool(query=needle, path=path, glob=glob, max_results=120))
            else:
                self.serve_static(route)
        except AppError as exc:
            self.send_error_json(str(exc), exc.status)
        except Exception as exc:
            self.send_error_json(str(exc), 500, traceback.format_exc(limit=5))

    def do_POST(self) -> None:
        try:
            self.prepare_request_context()
            parsed = urllib.parse.urlparse(self.path)
            payload = self.read_json_body()
            if parsed.path == "/api/chat":
                self.send_json(chat_agent(payload))
            elif parsed.path == "/api/chat/stream":
                self.stream_chat(payload)
            elif parsed.path == "/api/update/install":
                self.send_json(install_verified_update())
            elif parsed.path == "/api/settings":
                self.send_json(update_app_settings(payload))
            elif parsed.path == "/api/projects/open":
                project = open_project(str(payload.get("path") or ""))
                projects = list_projects_with_chats()
                self.send_json({"project": project, "projects": projects, "recent": projects, "chats": list_chat_records()})
            elif parsed.path == "/api/projects/pick":
                picked = pick_folder_native(str(payload.get("initial_path") or workspace_root(required=False) or CWD_WORKSPACE))
                if not picked:
                    self.send_json({"cancelled": True, "project": project_summary(), "projects": list_projects_with_chats(), "chats": list_chat_records()})
                else:
                    project = open_project(picked)
                    projects = list_projects_with_chats()
                    self.send_json({"cancelled": False, "project": project, "projects": projects, "recent": projects, "chats": list_chat_records()})
            elif parsed.path == "/api/files/pick":
                file_info = pick_file_native(str(payload.get("initial_path") or workspace_root(required=False) or CWD_WORKSPACE))
                self.send_json({"cancelled": not bool(file_info), "file": file_info})
            elif parsed.path == "/api/access":
                state = load_user_state()
                state["full_access"] = bool(payload.get("full_access"))
                save_user_state(state)
                self.send_json({"full_access": has_full_access(), "allow_commands": ALLOW_COMMANDS or has_full_access()})
            elif parsed.path == "/api/chats":
                chat = create_chat_record(str(payload.get("title") or "Новый чат"))
                self.send_json(public_chat_record(chat), 201)
            elif parsed.path.startswith("/api/chats/") and parsed.path.endswith("/rename"):
                chat_id = parsed.path.split("/")[-2]
                chat = load_chat_record(chat_id)
                title = str(payload.get("title") or "").strip()
                if not title:
                    raise AppError("title is required", 400)
                chat["title"] = title[:80]
                chat["updated_at"] = now_iso()
                save_chat_record(chat)
                self.send_json(public_chat_record(chat))
            elif parsed.path == "/api/file":
                target = resolve_user_path(str(payload.get("path") or ""))
                content = str(payload.get("content") or "")
                self.send_json(write_text_file(target, content, create_dirs=True))
            elif parsed.path == "/api/reset":
                session_id = str(payload.get("session_id") or "")
                if session_id:
                    delete_chat_record(session_id)
                self.send_json({"ok": True, "session_id": None, "chats": list_chat_records()})
            else:
                raise AppError(f"Unknown endpoint: {parsed.path}", 404)
        except AppError as exc:
            self.send_error_json(str(exc), exc.status)
        except Exception as exc:
            self.send_error_json(str(exc), 500, traceback.format_exc(limit=5))

    def do_DELETE(self) -> None:
        try:
            self.prepare_request_context()
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            if parsed.path.startswith("/api/chats/"):
                chat_id = parsed.path.rsplit("/", 1)[-1]
                has_project_path = "project_path" in query
                project_path = (query.get("project_path") or [""])[0]
                root = Path(project_path).expanduser().resolve() if project_path else None
                if root and (not root.exists() or not root.is_dir()):
                    raise AppError(f"Project folder not found: {root}", 404)
                self.send_json(delete_chat_record(chat_id, root=root, general=has_project_path and not project_path))
            elif parsed.path == "/api/projects":
                project_path = (query.get("path") or [""])[0]
                self.send_json(forget_project(project_path))
            else:
                raise AppError(f"Unknown endpoint: {parsed.path}", 404)
        except AppError as exc:
            self.send_error_json(str(exc), exc.status)
        except Exception as exc:
            self.send_error_json(str(exc), 500, traceback.format_exc(limit=5))

    def serve_static(self, route: str) -> None:
        if route in {"", "/"}:
            relative = "index.html"
        elif route == "/dashboard":
            relative = "dashboard.html"
        else:
            relative = route.lstrip("/")
        target = (WEB_DIR / relative).resolve()
        try:
            target.relative_to(WEB_DIR)
        except ValueError as exc:
            raise AppError("Static path escapes web root", 403) from exc
        if not target.exists() or not target.is_file():
            raise AppError("Not found", 404)
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type = f"{content_type}; charset=utf-8"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    set_current_user(DEFAULT_USER_ID)
    if not user_state_file().exists():
        set_user_workspace(CWD_WORKSPACE)
    host = os.environ.get("CODEX_LITE_HOST") or "127.0.0.1"
    port = int(os.environ.get("CODEX_LITE_PORT") or "8787")
    root = workspace_root(required=False)
    if root:
        remember_project(root)
    print("Serplex server")
    print(f"  UI:        http://{host}:{port}")
    print(f"  Workspace: {workspace_root(required=False) or 'none'}")
    print(f"  Ollama:    {OLLAMA_BASE_URL}")
    print(f"  Model:     {DEFAULT_MODEL}")
    print(f"  Commands:  {'enabled' if ALLOW_COMMANDS else 'disabled'}")
    print(f"  Access:    {'full PC' if has_full_access() else 'project sandbox'}")
    print("")
    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Serplex server...")
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

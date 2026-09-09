# ============================================================
# 文件名: run_sc_tse_integration_v0001.py
# 中文名: SC-TSE Integration MVP 本地入口
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / frontend / transport
# 脚本定位: 静态前端与结构化后端接口的本地 HTTP 传输
#
# 职责说明:
# - 提供项目载入与整链重算接口
#
# 本脚本做什么:
# - 传输 JSON，限制同源请求、请求体大小和同时计算数
#
# 本脚本不做什么:
# - 不计算布局或道路，不提供任意文件读取接口
#
# 制度边界声明:
# - 仅监听回环地址；无运行登记或数据库写入
# - 错误结构化；不把失败转换为成功
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: run_sc_tse_integration_v0001
# family: run_sc_tse_integration
# role: integration_local_http_entry
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/frontend/run_sc_tse_integration_v0001.py
# input:
#   - integration_config_v0001.json
#   - local HTTP requests
# output:
#   - versioned JSON responses and static UI
# depends_on:
#   - sc_tse_integration_api_v0001
#   - Python stdlib
# used_by:
#   - launch_sc_tse_integration_v0001
#   - integration_app_v0001
# ============================================================

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sc_tse_integration_api_v0001 import IntegrationInputError, load_project, read_json, run_request, scene, validate_project

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "run_sc_tse_integration"
SCRIPT_NAME = "run_sc_tse_integration_v0001"
SCRIPT_VERSION = "v0001"
BASE = Path(__file__).resolve().parent


# ============================================================
# 异常类型 / 工具函数区
# ============================================================

class IntegrationServerError(ValueError):
    pass


def load_config(path: Path) -> dict:
    config = read_json(path)
    if (set(config) != {"schema_version", "host", "port", "max_request_bytes"} or
            config["schema_version"] != "sc-tse-integration-config-v0001" or config["host"] != "127.0.0.1" or
            type(config["port"]) is not int or not 1024 <= config["port"] <= 65535 or
            type(config["max_request_bytes"]) is not int or not 1024 <= config["max_request_bytes"] <= 2097152):
        raise IntegrationServerError("invalid local server configuration")
    return config


def error_payload(kind: str, detail: str, request_id=None) -> dict:
    return {"schema_version": "sc-tse-integration-error-v0001", "status": "ERROR",
            "request_id": request_id, "error_type": kind, "detail": detail}


# ============================================================
# 默认映射
# ============================================================

STATIC = {"/": ("integration.html", "text/html; charset=utf-8"),
          "/integration.css": ("integration.css", "text/css; charset=utf-8"),
          "/integration_app_v0001.js": ("integration_app_v0001.js", "text/javascript; charset=utf-8")}


# ============================================================
# 核心类
# ============================================================

class IntegrationServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, config):
        self.config = config
        self.compute_lock = threading.Lock()
        super().__init__((config["host"], config["port"]), Handler)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, fmt, *args):
        print(fmt % args, file=sys.stderr)

    def reply(self, payload, status=200, mime="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(DEFAULT_ENCODING)
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed_host(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if not self.allowed_host():
            return self.reply(error_payload("HostError", "loopback host required"), 403)
        try:
            if self.path in STATIC:
                name, mime = STATIC[self.path]
                return self.reply((BASE / name).read_bytes(), mime=mime)
            if self.path == "/api/project":
                project = load_project()
                return self.reply({"project": project, "scene": scene(project["case"])})
            return self.reply(error_payload("NotFound", "unknown resource"), 404)
        except Exception as exc:
            return self.reply(error_payload(type(exc).__name__, str(exc)), 500)

    def do_POST(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        if (not self.allowed_host() or self.headers.get("X-SC-TSE") != "integration-v1" or
                self.headers.get("Origin", origin) != origin):
            return self.reply(error_payload("OriginError", "same-origin request required"), 403)
        if self.path not in ("/api/run", "/api/project"):
            return self.reply(error_payload("NotFound", "unknown endpoint"), 404)
        request_id = None
        try:
            if self.headers.get_content_type() != "application/json":
                raise IntegrationInputError("application/json required")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= self.server.config["max_request_bytes"]:
                return self.reply(error_payload("BodySizeError", "request must be 1 byte to 2 MiB"), 413)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise IntegrationInputError("incomplete request body")
            payload = json.loads(raw, parse_constant=lambda x: (_ for _ in ()).throw(IntegrationInputError("finite JSON required")))
            if self.path == "/api/project":
                validate_project(payload)
                return self.reply({"project": payload, "scene": scene(payload["case"])})
            if isinstance(payload, dict) and isinstance(payload.get("request_id"), str):
                request_id = payload["request_id"]
            if not self.server.compute_lock.acquire(blocking=False):
                return self.reply(error_payload("BusyError", "another complete run is in progress", request_id), 409)
            try:
                result = run_request(payload)
            finally:
                self.server.compute_lock.release()
            return self.reply(result)
        except (ValueError, UnicodeError) as exc:
            return self.reply(error_payload(type(exc).__name__, str(exc), request_id), 400)
        except Exception as exc:
            return self.reply(error_payload(type(exc).__name__, str(exc), request_id), 500)


# ============================================================
# CLI / main 接口区
# ============================================================

def _build_parser():
    parser = argparse.ArgumentParser(description="SC-TSE Integration MVP")
    parser.add_argument("--config", type=Path, default=BASE / "integration_config_v0001.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--open", action="store_true", help="open the local UI after binding")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        load_project()
        for name, _ in STATIC.values():
            if not (BASE / name).is_file():
                raise IntegrationServerError(f"missing UI asset: {name}")
        if args.dry_run:
            print(json.dumps({"status": "dry_run", "backend": "run_sc_tse_orchestrator_v0004"}))
            return 0
        with IntegrationServer(config) as server:
            print(json.dumps({"status": "ONLINE", "url": f"http://127.0.0.1:{server.server_port}"}), flush=True)
            if args.open:
                webbrowser.open(f"http://127.0.0.1:{server.server_port}")
            server.serve_forever()
        return 0
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps(error_payload(type(exc).__name__, str(exc))))
        return 2
    except Exception as exc:
        print(json.dumps(error_payload(type(exc).__name__, str(exc))))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

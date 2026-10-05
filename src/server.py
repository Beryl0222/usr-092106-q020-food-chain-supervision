"""薄 HTTP 接口（标准库 http.server，无第三方依赖）。

访问者身份通过请求头传递（部署时应替换为网关注入的可信身份）：
- ``X-Actor-Kind``: department | business | platform | public
- ``X-Actor-Id``  : 部门名 / 主体编号 / 平台名（public 可省略）

路由：
- POST   /events                      报送领域事件（跨部门按 event_id 幂等去重）
- GET    /events                      事件流（自动按访问者遮蔽）
- GET    /shops/{id}/dossier          电子证照 vs 实际经营地点分别核验
- GET    /lots/{id}/trace             双向追溯
- GET    /samples/{id}/scope          快检阳性后在途/在售/待履约/已送达范围
- GET    /recalls/{id}                召回数量与去向持续核对
- GET    /cases/{id}/audit            处罚反查：证据保管、规则版本、每次交接
- GET    /alerts                      公众风险提示（仅已核实发布）
- GET    /healthz
"""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import catalog
from .app import ChainSupervisionService
from .errors import AccessDenied, ConflictError, DomainError, ValidationError


def build_service(db_path: str | None = None) -> ChainSupervisionService:
    return ChainSupervisionService(db_path)


class _Handler(BaseHTTPRequestHandler):
    service: ChainSupervisionService = None  # 由 make_server 注入

    # -------------------------------------------------- 基础

    def log_message(self, fmt, *args):  # 保持测试输出干净
        pass

    def _send(self, code: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _actor(self) -> dict:
        kind = self.headers.get("X-Actor-Kind", "public")
        actor_id = self.headers.get("X-Actor-Id", "")
        if kind == "department":
            return {"kind": "department", "department": actor_id}
        if kind == "business":
            return {"kind": "business", "business_id": actor_id}
        if kind == "platform":
            return {"kind": "platform", "platform": actor_id}
        return {"kind": "public"}

    def _error(self, exc: Exception) -> None:
        from .errors import RiskLotFrozenError as _F
        if isinstance(exc, ValidationError):
            code = 422
        elif isinstance(exc, AccessDenied):
            code = 403
        elif isinstance(exc, _F):
            code = 423  # Locked：控制令期间风险批次被冻结
        elif isinstance(exc, ConflictError):
            code = 409
        else:
            code = 400
        payload = {"error": type(exc).__name__,
                   "detail": getattr(exc, "errors", None) or str(exc)}
        self._send(code, payload)

    # -------------------------------------------------- 读

    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        path, qs = parts.path, parse_qs(parts.query)
        svc = self.service
        try:
            if path == "/healthz":
                self._send(200, {"ok": True, "events": len(svc)})
            elif path == "/alerts":
                self._send(200, svc.public_alerts())
            elif path == "/events":
                self._send(200, svc.events_for(
                    self._actor(),
                    qs.get("aggregate_type", [None])[0],
                    qs.get("aggregate_id", [None])[0]))
            elif m := re.fullmatch(r"/shops/([^/]+)/dossier", path):
                self._send(200, svc.shop_dossier(m.group(1), self._actor()))
            elif m := re.fullmatch(r"/lots/([^/]+)/trace", path):
                self._send(200, svc.trace(m.group(1), self._actor()))
            elif m := re.fullmatch(r"/samples/([^/]+)/scope", path):
                self._send(200, svc.emergency_scope(m.group(1), self._actor()))
            elif m := re.fullmatch(r"/recalls/([^/]+)", path):
                self._send(200, svc.recall_status(m.group(1), self._actor()))
            elif m := re.fullmatch(r"/cases/([^/]+)/audit", path):
                self._send(200, svc.penalty_audit(m.group(1), self._actor()))
            else:
                self._send(404, {"error": "NotFound", "detail": path})
        except DomainError as exc:
            self._error(exc)

    # -------------------------------------------------- 写

    def do_POST(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        if parts.path != "/events":
            self._send(404, {"error": "NotFound", "detail": parts.path})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            record = json.loads(self.rfile.read(length).decode("utf-8"))
            stored = self.service.emit(record, self._actor())
            # 事件 seq 属于仓库内部序号，不对外暴露也无妨，保留在响应里
            self._send(201, stored)
        except json.JSONDecodeError as exc:
            self._send(400, {"error": "BadJson", "detail": str(exc)})
        except DomainError as exc:
            self._error(exc)
        except Exception as exc:  # 防御性兜底：不泄露连接中断
            self._send(500, {"error": "InternalError", "detail": str(exc)})


def make_server(host: str = "127.0.0.1", port: int = 8080,
                db_path: str | None = None) -> ThreadingHTTPServer:
    service = build_service(db_path)
    handler = type("BoundHandler", (_Handler,), {"service": service})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.service = service  # type: ignore[attr-defined]
    return httpd


def main(argv: list[str] | None = None) -> None:
    import argparse
    import os

    ap = argparse.ArgumentParser(description="食品全链监督后端")
    ap.add_argument("--host", default=os.environ.get("FCS_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("FCS_PORT", "8080")))
    ap.add_argument("--db", default=os.environ.get("FCS_DB", "data/eventlog.jsonl"))
    args = ap.parse_args(argv)
    httpd = make_server(args.host, args.port, args.db)
    print(f"食品全链监督后端监听 http://{args.host}:{args.port}（事件库 {args.db}）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


if __name__ == "__main__":
    main()

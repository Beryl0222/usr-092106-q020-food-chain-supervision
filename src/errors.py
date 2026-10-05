"""全链监督后端共用异常类型。"""


class DomainError(Exception):
    """所有领域错误的基类。"""


class ValidationError(DomainError):
    """事件形态校验失败（写入前拒绝）。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("；".join(errors))


class ConflictError(DomainError):
    """引用不存在、数量不守恒、状态不允许等业务不变量冲突。"""


class RiskLotFrozenError(DomainError):
    """风险批次在控制令期间被平台/企业指令要求移动、销售或换包。"""


class AccessDenied(DomainError):
    """访问者法定职责或归属范围不覆盖所请求的资源。"""

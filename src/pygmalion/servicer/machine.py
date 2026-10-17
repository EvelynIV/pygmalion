"""设备服务入口；设备实现由 machine 包负责。"""

from ..machine.errors import MachineError
from .errors import ServiceError


def _invoke(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except MachineError as exc:
        raise ServiceError(exc.kind, exc.payload) from exc


def begin_session(context):
    return context.machine.begin_session()


def end_session(context, session):
    context.machine.end_session(session)


def manual_status(context):
    return context.machine.manual_status()


def estop(context, data, *, session=None):
    context.printer.cancel_active()
    return _invoke(context.machine.estop, data)


def recover(context, data, *, session=None):
    return _invoke(context.machine.recover, data, session=session)


def serial_ports(context, *, session=None):
    return _invoke(context.machine.serial_ports)


def set_serial_port(context, data, *, session=None):
    return _invoke(context.machine.set_serial_port, data, session=session)


def detect_serial_port(context, data, *, session=None):
    return _invoke(context.machine.detect_serial_port, data, session=session)


def inspect(context, *, session=None):
    return _invoke(context.machine.inspect, session=session)


def jog(context, data, *, session=None):
    return _invoke(context.machine.jog, data, session=session)


def backup(context, *, session=None):
    return _invoke(context.machine.backup, session=session)


def write_settings(context, data, *, session=None):
    return _invoke(context.machine.write_settings, data, session=session)

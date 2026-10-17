"""组装领域资源并委托其生命周期。"""

from .. import config, storage
from ..jobs import repository as jobs
from ..jobs.service import PrinterService
from ..machine.controller import DeviceController


class ServiceContext:
    def __init__(self, *, dry_run: bool = False):
        self.machine = DeviceController()
        self.printer = PrinterService(dry_run=dry_run, controller=self.machine)

    def start(self):
        config.ensure_dirs()
        storage.init_db()
        jobs.interrupt_unfinished_jobs()
        self.printer.start()

    def stop(self):
        try:
            self.printer.stop()
        finally:
            self.machine.stop()

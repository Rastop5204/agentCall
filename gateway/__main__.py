import logging
import os
import signal
import threading

from gateway.service import Gateway
from gateway.store import Store
from gateway.server import make_server


def main():
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    store = Store(os.environ.get('AGENTCALL_DATA_DIR', os.environ.get('EMAILCALL_DATA_DIR', 'data')))
    app = Gateway(store)
    server = make_server(app, os.environ.get('AGENTCALL_HOST', os.environ.get('EMAILCALL_HOST', '127.0.0.1')), int(os.environ.get('AGENTCALL_PORT', os.environ.get('EMAILCALL_PORT', '10086'))))
    shutting_down = threading.Event()

    def stop(signum, frame):
        if not shutting_down.is_set():
            shutting_down.set()
            app.stop_event.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    app.start()
    logging.info('agentCall ready at http://localhost:%s/frontend/', server.server_address[1])
    try:
        server.serve_forever(poll_interval=.25)
    finally:
        app.stop()
        server.server_close()
        # Do not close SQLite underneath an in-flight SMTP/IMAP worker. SQLite recovers its
        # journal on next boot; sending rows are deliberately reconciled as uncertain.
        if not any(thread.is_alive() for thread in app.threads):
            store.close()


if __name__ == '__main__':
    main()

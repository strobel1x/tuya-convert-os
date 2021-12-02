
import os
import logging

from typing import Awaitable, List, Optional

from tornado.escape import json_decode, to_unicode
from tornado.httpclient import AsyncHTTPClient, HTTPClientError
from tornado.web import Application, RequestHandler, HTTPError
from tornado.websocket import WebSocketHandler
from tornado.ioloop import IOLoop
from tornado.process import Subprocess

from tcfrontend import states
from tcfrontend import tccontrol
from tcfrontend import VERSION


logger = logging.getLogger(__name__)


class MainPageHandler(RequestHandler):
    def get(self) -> None:
        self.render('main.html', version=VERSION)


class JSONRequestHandlerMixin:
    json = None
    request = None

    def prepare(self):
        if self.request.headers.get('Content-Type') == 'application/json':
            self.json = json_decode(self.request.body)


class StatusHandler(JSONRequestHandlerMixin, RequestHandler):
    def get(self) -> None:
        self.set_header('Cache-Control', 'no-cache, no-store, must-revalidate, max-age=0')

        self.finish({
            'state': states.get_state(),
            'params': states.get_state_params()
        })

    async def patch(self) -> None:
        if self.json is None:
            raise HTTPError(400, 'expected JSON in request body')

        state = self.json.get('state')
        if state not in states.STATES:
            raise HTTPError(400, f'invalid state {state}')

        params = self.json.get('params', {})

        try:
            await states.request_state(state, **params)

        except states.InvalidTransitionRequest:
            raise HTTPError(400, f'invalid transition')


class FirmwareOriginalHandler(RequestHandler):
    def get(self) -> None:
        details = tccontrol.get_conversion_details()
        if details is None or details.get('original_firmware') is None:
            raise HTTPError(400, 'original firmware not available')

        self.set_header('Content-Type', 'application/octet-stream')
        self.set_header('Cache-Control', 'no-cache, no-store, must-revalidate, max-age=0')
        self.set_header('Content-Disposition', 'attachment; filename="original.bin"')

        self.finish(details['original_firmware'])


class FirmwareProxyHandler(RequestHandler):
    async def get(self) -> None:
        http_client = AsyncHTTPClient()
        url = self.get_argument('url')
        logger.debug('proxy downloading firmware file at %s', url)

        try:
            response = await http_client.fetch(url)

        except HTTPClientError as e:
            logger.error('failed to download file at %s', url, exc_info=True)

            raise HTTPError(status_code=e.code)

        except Exception:
            logger.error('failed to download file at %s', url, exc_info=True)

            raise

        else:
            self.set_header('Content-Type', 'application/octet-stream')
            self.set_header('Cache-Control', 'no-cache, no-store, must-revalidate, max-age=0')
            await self.finish(response.body)

class LogHandler(WebSocketHandler):
    connected = False

    async def tail(self):
        self.process = Subprocess(["journalctl", "-u", "tcfrontend", "-f"], stdout=Subprocess.STREAM)
        while self.connected:
            line = await self.process.stdout.read_until(b"\n")
            self.write_message(to_unicode(line) + u"\n")

    def start_tail(self) -> None:
        IOLoop.current().spawn_callback(self.tail)

    def open(self) -> None:
        self.connected = True
        self.start_tail()

    def on_close(self) -> None:
        self.connected = False

def make_handlers() -> List[tuple]:
    return [
        (r'/', MainPageHandler),
        (r'/status', StatusHandler),
        (r'/firmware/original.bin', FirmwareOriginalHandler),
        (r'/firmware/proxy', FirmwareProxyHandler),
        (r'/logs', LogHandler)
    ]


def make_app() -> Application:
    return Application(
        handlers=make_handlers(),
        template_path=os.path.join(os.path.dirname(__file__), 'templates'),
        static_path=os.path.join(os.path.dirname(__file__), 'static'),
        debug=False,
        compiled_template_cache=False
    )

"""Hardware-free regressions. Run: python3 -m unittest discover -s tests/unit -v.

Service methods are compiled directly from their AST so these tests exercise the
production control flow without importing GPU, Kafka, or robot dependencies.
"""
import ast
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[2]
BOT = 'interaction-manager-service/src/robots/photo_booth_bot.py'
MAIN = 'interaction-manager-service/src/main.py'
CAMERA = 'camera-service/src/main.py'
spec = importlib.util.spec_from_file_location(
    'photo_gate', ROOT / 'workmesh/src/workmesh/photo_gate.py'
)
gate_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate_module)
PhotoGate = gate_module.PhotoGate


def load_method(path, name, env):
    tree = ast.parse((ROOT / path).read_text())
    method = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    method.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module='__future__',
        names=[ast.alias(name='annotations')], level=0), method], type_ignores=[])
    namespace = dict(env)
    exec(compile(ast.fix_missing_locations(module), path, 'exec'), namespace)
    return namespace[name]


def detection(index=1, x=.25, y=.15, width=.5, height=.7):
    return NS(marker_id=0, bounding_boxes=[NS(
        frame_index=index, top_left_x=x, top_left_y=y,
        width=width, height=height, score=.99)])


class GateTests(unittest.TestCase):
    def test_missing_centered_and_stale(self):
        gate = PhotoGate()
        self.assertFalse(gate.ready(now=10))
        gate.observe(detection(), now=10)
        self.assertTrue(gate.ready(now=11))
        self.assertFalse(gate.ready(now=12))
        self.assertFalse(gate.ready(now=11, after=10.5))

    def test_replayed_frame_cannot_refresh_presence(self):
        gate = PhotoGate()
        gate.observe(detection(), now=10)
        gate.observe(detection(), now=20)
        self.assertFalse(gate.ready(now=20))

    def test_offcenter_small_and_disappeared(self):
        gate = PhotoGate()
        gate.observe(detection(x=0), now=10)
        self.assertFalse(gate.ready(now=10))
        gate.observe(detection(2, x=.45, y=.45, width=.1, height=.1), now=11)
        self.assertFalse(gate.ready(now=11))
        gate.observe(detection(3), now=12)
        gate.clear()
        self.assertFalse(gate.ready(now=12))

    def test_invalid_and_empty_detections(self):
        gate = PhotoGate()
        gate.observe(detection(x=float('nan')), now=10)
        self.assertFalse(gate.ready(now=10))
        gate.observe(NS(marker_id=0, bounding_boxes=[]), now=10)
        self.assertFalse(gate.ready(now=10))

    def test_capture_presence_does_not_require_body_center_alignment(self):
        gate = PhotoGate()
        # A face/neck can be centered while the visible body extends below it.
        gate.observe(detection(x=.4, y=.5, width=.3, height=.4), now=10)
        self.assertFalse(gate.ready(now=10))
        self.assertTrue(gate.ready(now=10, require_centered=False))
        self.assertFalse(gate.ready(now=12, require_centered=False))
        gate.clear()
        self.assertFalse(gate.ready(now=10, require_centered=False))

    def test_speaking_asset_has_pitch_but_no_sideways_rotation(self):
        path = ROOT / 'animation-database-service/assets/animLibrary/talkingForward/talkingForward.json'
        data = json.loads(path.read_text())['data']
        frames = data['head_rotation']['frames']
        self.assertTrue(any(abs(f[1]) > .1 for f in frames))
        self.assertTrue(all(f[0] == f[2] == 0 and abs(f[1]) <= 12 for f in frames))
        self.assertTrue(all(v == 0 for v in data['body_angle']['frames']))
        self.assertEqual({len(v['frames']) for v in data.values()}, {len(frames)})


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_camera_run_consumes_detections_and_cleans_up_at_eof(self):
        real_sleep = asyncio.sleep
        async def tick(_delay):
            await real_sleep(0)
        method = load_method(CAMERA, 'run', {
            'asyncio': NS(sleep=tick, gather=asyncio.gather)})
        gate = PhotoGate()
        stopped = asyncio.Event()
        service = NS(_is_eof=False, create_task=asyncio.create_task)
        async def consume():
            try:
                gate.observe(detection(100))
                service._is_eof = True
                await asyncio.Event().wait()
            finally:
                stopped.set()
        service._start_consuming = consume
        await method(service)
        self.assertTrue(gate.ready(require_centered=False))
        self.assertTrue(stopped.is_set())

    async def test_camera_rejects_missing_stale_and_old_source_frames(self):
        method = load_method(CAMERA, 'handle_capture_request', {
            'web': NS(Response=lambda **kw: NS(**kw))})
        camera = Mock()
        camera.read.return_value = (False, None)
        service = NS(_photo_gate=PhotoGate(), _frame_id=100,
                     _config=NS(fps=30), _camera=camera, logger=Mock())
        response = await method(service, None)
        self.assertEqual(response.status, 409)
        camera.read.assert_not_called()
        service._photo_gate.observe(detection(1))
        response = await method(service, None)
        self.assertEqual(response.status, 409)
        camera.read.assert_not_called()
        service._photo_gate.observe(detection(100, x=.4, y=.5, width=.3, height=.4))
        response = await method(service, None)
        self.assertEqual(response.status, 500)  # Guard passes; fake camera read fails.
        camera.read.assert_called_once()

    async def test_wait_requires_new_frame_and_times_out_without_one(self):
        method = load_method(BOT, 'wait_for_photo_subject', {
            'asyncio': asyncio, 'time': __import__('time')})
        bot = NS(photo_gate=PhotoGate(), config=NS(center_user_timeout=.02))
        bot.photo_gate.observe(detection())
        with self.assertRaises(TimeoutError):
            await method(bot)
        bot.config.center_user_timeout = .2
        async def fresh_frame():
            await asyncio.sleep(.01)
            bot.photo_gate.observe(detection(2, x=.4, y=.5, width=.3, height=.4))
        await asyncio.gather(method(bot), fresh_frame())

    async def test_speaking_pauses_tracking_and_restores_listening_on_error(self):
        method = load_method(BOT, 'handle_talking', {'ServiceName': NS(STT='stt')})
        bot = NS(robot_name='test', _logger=Mock(), light_manager=NS(light_off=AsyncMock()),
            is_talking_lock=asyncio.Lock(), _is_talking=False,
            config=NS(enable_listening_while_speaking=False),
            _tracking_animation_uuid='track', state='track', States=NS(track='track'),
            track_pause=AsyncMock(), service_off=AsyncMock(), service_on=AsyncMock(),
            request_human_speech=AsyncMock(return_value='speech'),
            wait_for_clip_started=AsyncMock(return_value=True),
            play_clip=AsyncMock(return_value='talk'),
            wait_for_clip=AsyncMock(side_effect=RuntimeError('playback failed')),
            stop_clip=AsyncMock())
        with self.assertRaisesRegex(RuntimeError, 'playback failed'):
            await method(bot, 'hello', light_on=False, look_direction='left')
        self.assertEqual(bot.play_clip.call_args.kwargs['clip_name'], 'talkingForward')
        self.assertEqual(bot.track_pause.await_args_list[0].args, ('track',))
        self.assertEqual(bot.track_pause.await_args_list[-1].kwargs, {'enable': False})
        bot.service_on.assert_awaited_once_with('stt')
        self.assertFalse(bot._is_talking)
        bot.stop_clip.side_effect = RuntimeError('stop failed')
        with self.assertRaisesRegex(RuntimeError, 'stop failed'):
            await method(bot, 'hello', light_on=False)
        self.assertFalse(bot._is_talking)
        self.assertEqual(bot.service_on.await_count, 2)

    async def test_photo_branch_never_acknowledges_failed_search_or_countdown(self):
        tree = ast.parse((ROOT / MAIN).read_text())
        branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
            and ast.unparse(n.test) == 'tool_name in take_picture_tools'
            and '_find_user' in ast.unparse(n.body))
        wrapper = ast.parse('async def run(self, message):\n    for _ in range(1):\n        pass').body[0]
        wrapper.body[0].body = branch.body
        status = NS(TOOL_CALL_FAILED=2)
        env = {'asyncio': asyncio, 'time': __import__('time'), 'tool_name': 'look_at_human',
               'ToolStatus': lambda **kw: NS(**kw), 'tool_status_topic': 'status'}
        env['ToolStatus'].Status = status
        exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), MAIN, 'exec'), env)
        bot = NS(state='track', States=NS(take_picture='picture'),
            wait_for_photo_subject=AsyncMock(), safe_trigger_event=AsyncMock(),
            handle_prepare_for_picture=AsyncMock(), require_photo_subject=Mock(),
            handle_take_picture=AsyncMock())
        async def transition(name, **kwargs):
            bot.state = 'picture' if name == 'take_picture' else name
        bot.safe_trigger_event.side_effect = transition
        service = NS(photo_bot=bot, _find_user=AsyncMock(return_value=False),
            _speak=AsyncMock(), _send_tool_processed_message=AsyncMock(),
            publish=AsyncMock(), logger=Mock())
        message = NS(action_uuid='a', robot_id=0, name='look_at_human', status=0)
        await env['run'](service, message)
        service._send_tool_processed_message.assert_not_awaited()
        self.assertEqual(service.publish.call_args.args[1].status, 2)
        service._find_user.return_value = True
        bot.wait_for_photo_subject.side_effect = [None, TimeoutError()]
        await env['run'](service, message)
        service._send_tool_processed_message.assert_not_awaited()
        bot.handle_take_picture.assert_not_awaited()
        bot.wait_for_photo_subject.side_effect = None
        await env['run'](service, message)
        service._send_tool_processed_message.assert_awaited_once()
        bot.handle_take_picture.assert_awaited_once()

    async def test_repeated_photo_request_cannot_use_generic_ack(self):
        tree = ast.parse((ROOT / MAIN).read_text())
        branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
            and 'tool_name in self.running_tools' in ast.unparse(n.test))
        wrapper = ast.parse('async def run(self):\n    for _ in range(1):\n        pass').body[0]
        wrapper.body[0].body = [branch]
        env = {'tool_name': 'look_at_human', 'human_tools': [],
               'take_picture_tools': ['look_at_human'], 'message': object()}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), MAIN, 'exec'), env)
        service = NS(running_tools={'look_at_human': 0}, logger=Mock(),
                     _send_tool_processed_message=AsyncMock())
        await env['run'](service)
        service._send_tool_processed_message.assert_not_awaited()

    async def test_rejection_releases_ack_waiter(self):
        class ToolStatus:
            TOOL_CALL_PROCESSED = 3
            TOOL_CALL_FAILED = 2
        message = ToolStatus()
        message.action_uuid, message.status, message.response = 'a', 2, 'No person found'
        consumer = NS(subscribe=Mock(), consume=AsyncMock(return_value=(message, 'status')),
                      close=AsyncMock())
        method = load_method('agent-service/workflows/photo_booth_agent/src/photo_booth_agent/utils.py',
            'wait_for_event', {'asyncio': asyncio, 'logger': Mock(), 'ToolStatus': ToolStatus,
                               'Consumer': lambda: consumer})
        with self.assertRaisesRegex(RuntimeError, 'No person found'):
            await method(action_uuid='a', timeout=1, topic='status',
                         expected_type=ToolStatus, expected_status=3)
        consumer.close.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()

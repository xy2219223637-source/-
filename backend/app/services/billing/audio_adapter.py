"""Finite local audio test protocol; this is not a vendor protocol declaration."""
import base64
from hashlib import sha256
from io import BytesIO
import logging
import uuid
import wave

from app.services.billing.amounts import fail
from app.services.billing.metering import AdapterOutcome, KnownNoUsage

logger = logging.getLogger(__name__)


def local_wav(frames=8000):
    output = BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\x00\x10' * frames)
    return output.getvalue()


def inspect_wav(data):
    """Verify PCM bytes, not a container duration field or transcript estimate."""
    with wave.open(BytesIO(data), 'rb') as audio:
        frames, rate = audio.getnframes(), audio.getframerate()
        if (audio.getnchannels(), audio.getsampwidth(), rate, audio.getcomptype()) != (1, 2, 8000, 'NONE'):
            raise ValueError('Unsupported local PCM specification')
        # getnframes floors a partial final frame. Read one extra frame to
        # include all data bytes and reject both truncation and half frames.
        if len(audio.readframes(frames + 1)) != frames * 2:
            raise ValueError('Truncated or incomplete PCM frames')
        if frames * 1000 % rate:
            raise ValueError('The local protocol requires integral milliseconds')
    return {'frames': frames, 'sample_rate': rate,
            'duration_ms': frames * 1000 // rate,
            'sha256': sha256(data).hexdigest(), 'wav_base64': base64.b64encode(data).decode('ascii')}


class AudioMockAdapter:
    execution_mode = 'MOCK'
    specification = 'mock-audio:pcm8000:mono:v1'

    def __init__(self, capability, *, scenario='complete', max_polls=3, cleanup_failures=0):
        if capability not in ('TTS', 'ASR'):
            raise ValueError('Only TTS and ASR are supported')
        self.capabilities = (capability,)
        if scenario not in ('complete', 'partial', 'missing_usage', 'empty', 'timeout', 'no_usage', 'invalid_audio'):
            raise ValueError('Unsupported local test scenario')
        if type(max_polls) is not int or not 1 <= max_polls <= 10:
            raise ValueError('Local poll limit must be 1..10')
        if type(cleanup_failures) is not int or not 0 <= cleanup_failures <= 10:
            raise ValueError('Local cleanup failure count must be 0..10')
        self.scenario = scenario
        self.max_polls = max_polls
        self.cleanup_failures = cleanup_failures
        self.submit_count = 0
        self.poll_count = 0
        self.temporary_objects = {}
        self.cleanup_events = []

    def validate_request(self, request):
        if request.specification != self.specification or request.capability not in self.capabilities:
            fail('PRICE_SPEC_MISMATCH', '音频请求不符合本地模拟规格', 422)
        payload = request.payload
        try:
            if request.capability == 'TTS':
                if (set(payload) != {'text', 'resource'} or not isinstance(payload['text'], str) or
                        len(payload['text']) > 10000 or payload['resource'] != 'local-tone'):
                    raise ValueError('Invalid local TTS input')
                metric, quantity = 'character_count', len(payload['text'])
            else:
                if (set(payload) != {'audio_base64', 'route'} or payload['route'] != 'local' or
                        not isinstance(payload['audio_base64'], str) or len(payload['audio_base64']) > 1400000):
                    raise ValueError('Invalid local ASR input')
                data = base64.b64decode(payload['audio_base64'], validate=True)
                quantity = inspect_wav(data)['duration_ms'] if data else 0
                metric = 'audio_milliseconds'
            maximum = request.maximum_usage
            if (not isinstance(maximum, dict) or set(maximum) != {metric} or
                    type(maximum[metric]) is not int or not quantity <= maximum[metric] <= 10**9):
                raise ValueError('Invalid local audio bound')
        except (ValueError, TypeError, KeyError, wave.Error, EOFError):
            fail('PRICE_SPEC_MISMATCH', '音频素材、资源或用量上界不符合本地模拟规格', 422)

    def execute(self, request):
        self.validate_request(request)
        is_tts = request.capability == 'TTS'
        data = local_wav(4000 if self.scenario == 'partial' else 8000) if is_tts else base64.b64decode(request.payload['audio_base64'], validate=True)
        if self.scenario == 'no_usage' or (is_tts and not request.payload['text']) or (not is_tts and not data):
            raise KnownNoUsage('Local protocol rejected before submission')
        audio = None if is_tts else inspect_wav(data)
        if not is_tts and not audio['frames']:
            raise KnownNoUsage('Local protocol rejected an empty audio track')
        object_id = 'local-audio-' + uuid.uuid4().hex
        self.temporary_objects[object_id] = data
        try:
            self.submit_count += 1
            for index in range(self.max_polls):
                self.poll_count += 1
                if index > 0 and self.scenario != 'timeout':
                    break
            else:
                raise TimeoutError('Local polling bound exhausted; consumption unknown')
            text = request.payload['text'] if is_tts else '本地模拟转写'
            usage = {'character_count': len(text)} if is_tts else {'audio_milliseconds': audio['duration_ms']}
            status = 'COMPLETE'
            if is_tts:
                try:
                    audio = inspect_wav(b'controlled invalid audio' if self.scenario == 'invalid_audio' else data)
                    if not audio['frames']:
                        raise ValueError('Empty generated audio')
                except (ValueError, wave.Error, EOFError):
                    audio, usage, text, status = None, None, '', 'INVALID_AUDIO'
            if self.scenario == 'partial':
                if audio:
                    text, status = text[:1], 'PARTIAL'
            if self.scenario == 'empty':
                text, status = '', 'EMPTY'
                if is_tts:
                    audio, usage = None, None
            if self.scenario == 'missing_usage':
                usage = None
            if not is_tts and self.scenario == 'invalid_audio':
                audio, usage, text, status = None, None, '', 'INVALID_AUDIO'
        finally:
            cleanup = self.retry_cleanup()
        return AdapterOutcome({'text': text, 'audio': audio,
            'segments': [{'text': text, 'start_ms': 0, 'end_ms': audio['duration_ms']}] if text and audio else [],
            'output_status': status, 'cleanup': cleanup}, usage,
            provider_request_id=object_id, execution_status='SUCCEEDED' if status == 'COMPLETE' else 'FAILED')

    def retry_cleanup(self):
        """Retry deletion only; model execution and accounting are never retried."""
        if self.temporary_objects and self.cleanup_failures:
            self.cleanup_failures -= 1
            logger.warning('MOCK_AUDIO_CLEANUP_PENDING: %d local objects', len(self.temporary_objects))
        else:
            self.temporary_objects.clear()
        event = {'status': 'NEEDS_CLEANUP' if self.temporary_objects else 'CLEANED',
                 'pending': sorted(self.temporary_objects)}
        self.cleanup_events.append(event)
        return event

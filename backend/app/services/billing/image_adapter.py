"""Local IMAGE transport, with decoded artifacts as the image-count evidence.

The declared 8x8/local specification is synthetic, never a vendor declaration.
No SDK, URL downloader, or configured object-storage backend is involved.
"""
import base64
from hashlib import sha256
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from app.services.billing.amounts import fail
from app.services.billing.metering import AdapterOutcome


class ImageMockAdapter:
    execution_mode = 'MOCK'
    capabilities = ('IMAGE',)
    specification = 'mock-image:8x8:local'

    def __init__(self, *, images=None, usage_confirmed=True):
        if images is not None and (not isinstance(images, tuple) or len(images) > 16 or
                                   any(not isinstance(value, bytes) for value in images)):
            raise ValueError('Controlled image fixtures must be a bounded tuple of bytes')
        if type(usage_confirmed) is not bool:
            raise ValueError('Usage evidence must be explicit')
        self.images = images
        self.usage_confirmed = usage_confirmed

    def validate_request(self, request):
        payload = request.payload
        if (request.capability != 'IMAGE' or request.specification != self.specification or
                not isinstance(payload, dict) or
                (payload.get('width'), payload.get('height'), payload.get('quality')) != (8, 8, 'local') or
                type(payload.get('count')) is not int or not 1 <= payload['count'] <= 16 or
                not isinstance(request.maximum_usage, dict) or
                set(request.maximum_usage) != {'image_count'} or
                type(request.maximum_usage['image_count']) is not int or
                not payload['count'] <= request.maximum_usage['image_count'] <= 10**9):
            fail('PRICE_SPEC_MISMATCH', '图片请求不符合已登记的模拟计价规格或数量边界', 422)

    def execute(self, request):
        self.validate_request(request)
        images = self.images
        if images is None:
            images = []
            for index in range(request.payload['count']):
                output = BytesIO()
                Image.new('RGB', (8, 8), (index, 90, 180)).save(output, format='PNG')
                images.append(output.getvalue())
        artifacts = []
        invalid = []
        for index, data in enumerate(images):
            try:
                with Image.open(BytesIO(data)) as image:
                    if image.format != 'PNG' or image.size != (8, 8):
                        invalid.append({'child_index': index, 'reason': 'SPECIFICATION_MISMATCH'})
                        continue
                    image.load()
                    width, height = image.size
            except (UnidentifiedImageError, OSError, ValueError):
                invalid.append({'child_index': index, 'reason': 'UNDECODABLE'})
                continue
            artifacts.append({'child_index': index, 'width': width, 'height': height,
                'sha256': sha256(data).hexdigest(), 'png_base64': base64.b64encode(data).decode('ascii')})
        complete = len(artifacts) == request.payload['count'] and not invalid and self.usage_confirmed
        output_status = 'COMPLETE' if complete else 'PARTIAL' if artifacts else 'EMPTY'
        return AdapterOutcome({'images': artifacts, 'invalid_images': invalid, 'output_status': output_status},
            {'image_count': len(artifacts)} if self.usage_confirmed else None,
            provider_request_id='mock-image', execution_status='SUCCEEDED' if complete else 'FAILED')

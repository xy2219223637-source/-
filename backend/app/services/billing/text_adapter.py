"""Controlled TEXT/VISION transport; no SDK, network, or object storage."""
from app.services.billing.metering import AdapterOutcome


class TextVisionMockAdapter:
    execution_mode = 'MOCK'
    capabilities = ('TEXT', 'VISION')

    def execute(self, request):
        if request.capability == 'VISION':
            return AdapterOutcome({'description': '模拟视觉识别结果'}, {'input_tokens': 1000, 'output_tokens': 500},
                                  provider_request_id='mock-vision')
        return AdapterOutcome({'message': '模拟文本结果'}, {'input_tokens': 2000, 'output_tokens': 1000},
                              provider_request_id='mock-text')

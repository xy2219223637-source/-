"""Controlled embedding transport; input-only mock evidence, no vendor or storage I/O."""
import math
from dataclasses import dataclass, field, replace
from fastapi import HTTPException
from app.auth import AuthContext
from app.services.billing.amounts import fail
from app.services.billing.metering import AdapterOutcome, BillingContext, BoundedRequest
from app.services.billing.registry import UsageContract
from app.services.billing.service import digest


def embedding_request(texts, *, dimensions, maximum_input_tokens, bound_evidence):
    request = BoundedRequest('EMBEDDING', {'texts': texts, 'dimensions': dimensions},
                             {'input_tokens': maximum_input_tokens}, bound_evidence)
    EmbeddingMockAdapter.validate_request(request)
    return request


@dataclass(frozen=True)
class EmbeddingBatchOutcome:
    embeddings: list | None
    children: tuple
    complete: bool
    partial_embeddings: list = field(default_factory=list)
    failed_batch_index: int | None = None
    error_code: str | None = None


class EmbeddingBatchInterrupted(RuntimeError):
    """Unexpected storage/execution failure with confirmed earlier child evidence."""

    def __init__(self, partial_result):
        super().__init__('嵌入批次执行中断；此前已执行子订单见 partial_result')
        self.partial_result = partial_result


def execute_embedding_batches(service, context, model_key, batches, *, adapters):
    """Each transport-equivalent batch owns one stable child intent and order.

    The caller supplies a persisted parent business key and proven bounds per
    batch. Binding the entire plan into every child detects changed later inputs
    before an already-successful child can be replayed against another plan.
    """
    if not isinstance(context, BillingContext) or not isinstance(context.business_key, str):
        fail('BILLING_CONTEXT_REQUIRED', '嵌入批次缺少持久业务意图', 422)
    if (not isinstance(batches, (list, tuple)) or not batches or
            not isinstance(adapters, (list, tuple)) or len(batches) != len(adapters)):
        fail('PRICE_SPEC_MISMATCH', '嵌入批次和适配器必须逐批对应', 422)
    keys = [f'{context.business_key}:embedding:{index}' for index in range(len(batches))]
    if not context.business_key or any(len(key) > 200 for key in keys):
        fail('BILLING_CONTEXT_REQUIRED', '嵌入批次业务意图无效', 422)
    for request, adapter in zip(batches, adapters):
        if not isinstance(request, BoundedRequest) or not isinstance(adapter, EmbeddingMockAdapter):
            fail('PRICE_SPEC_MISMATCH', '模拟嵌入批次契约不匹配', 422)
        adapter.validate_request(request)
    plan = digest([{'payload': request.payload, 'maximum': request.maximum_usage,
                    'bound_evidence': request.bound_evidence, 'specification': request.specification}
                   for request in batches])
    children, vectors = [], []
    for index, (key, request, adapter) in enumerate(zip(keys, batches, adapters)):
        bounded = replace(request, payload={**request.payload, '_batch_intent': plan})
        try:
            outcome = service.execute_metered(replace(context, business_key=key), model_key, bounded, adapter=adapter)
            children.append(outcome)
            order = service.get_model_order(AuthContext(context.user_id, context.tenant_id, '', '', ''), outcome.order_id)
        except HTTPException as error:
            if not children:
                raise
            code = error.detail.get('code') if isinstance(error.detail, dict) else None
            return EmbeddingBatchOutcome(None, tuple(children), False, vectors, index, code or 'BATCH_REQUEST_REJECTED')
        except Exception as error:
            if not children:
                raise
            partial = EmbeddingBatchOutcome(None, tuple(children), False, vectors, index, 'BATCH_EXECUTION_INTERRUPTED')
            raise EmbeddingBatchInterrupted(partial) from error
        if outcome.execution_status != 'SUCCEEDED':
            return EmbeddingBatchOutcome(None, tuple(children), False, vectors, index, 'EXECUTION_' + outcome.execution_status)
        if outcome.usage['source'] == 'UNKNOWN':
            return EmbeddingBatchOutcome(None, tuple(children), False, vectors, index, 'USAGE_UNKNOWN')
        if outcome.usage['input_tokens'] > request.maximum_usage['input_tokens']:
            return EmbeddingBatchOutcome(None, tuple(children), False, vectors, index, 'USAGE_EXCEEDS_RESERVATION')
        if order['status'] != 'SETTLED':
            return EmbeddingBatchOutcome(None, tuple(children), False, vectors, index, 'SETTLEMENT_PENDING')
        vectors.extend(outcome.result['embeddings'])
    return EmbeddingBatchOutcome(vectors, tuple(children), True)


class EmbeddingMockAdapter:
    execution_mode = 'MOCK'
    capabilities = ('EMBEDDING',)
    usage_contract = UsageContract(('input_tokens',), (), 'billing/embedding_adapter:controlled-mock-v1')

    def __init__(self, response):
        self.response = response

    @staticmethod
    def validate_request(request):
        if not isinstance(request.bound_evidence, str) or not request.bound_evidence.strip():
            fail('USAGE_BOUND_UNKNOWN', '缺少可证明的输入 Token 上界依据', 422)
        payload = request.payload
        if request.capability != 'EMBEDDING' or not isinstance(payload, dict):
            fail('PRICE_SPEC_MISMATCH', '嵌入请求能力或结构不匹配', 422)
        texts, dimensions = payload.get('texts'), payload.get('dimensions')
        # This is a bounded local MOCK shape, not a declaration of supplier support.
        if (not isinstance(texts, list) or not texts or
                any(not isinstance(text, str) or not text for text in texts) or
                type(dimensions) is not int or not 1 <= dimensions <= 65536):
            fail('PRICE_SPEC_MISMATCH', '模拟嵌入请求必须含非空文本和受限向量维度', 422)
        usage = request.maximum_usage
        if (not isinstance(usage, dict) or set(usage) != {'input_tokens'} or
                type(usage['input_tokens']) is not int or not 0 <= usage['input_tokens'] <= 10**9):
            fail('PRICE_SPEC_MISMATCH', '嵌入仅接受可证明的输入 Token 上界', 422)

    def execute(self, request):
        response = self.response if isinstance(self.response, dict) else {}
        reported_usage = response.get('usage')
        quantity = reported_usage.get('prompt_tokens') if isinstance(reported_usage, dict) else None
        usage = {'input_tokens': quantity} if type(quantity) is int and 0 <= quantity <= 10**9 else None
        try:
            data = response['data']
            count, dimensions = len(request.payload['texts']), request.payload['dimensions']
            if not isinstance(data, list) or len(data) != count:
                raise ValueError('count')
            vectors = [None] * count
            for item in data:
                index, vector = item['index'], item['embedding']
                if type(index) is not int or not 0 <= index < count or vectors[index] is not None:
                    raise ValueError('index')
                if not isinstance(vector, list) or len(vector) != dimensions:
                    raise ValueError('dimensions')
                if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
                    raise ValueError('finite-numeric')
                vectors[index] = [float(value) for value in vector]
        except (KeyError, TypeError, ValueError, OverflowError):
            return AdapterOutcome({'embeddings': None, 'error': 'INVALID_EMBEDDING_RESPONSE'}, usage,
                                  execution_status='FAILED')
        return AdapterOutcome({'embeddings': vectors}, usage)

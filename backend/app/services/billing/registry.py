"""Redacted runtime identities and explicit, evidence-backed adapter declarations.

No provider clients, network probes or credential hashes belong in this registry.
"""
from dataclasses import dataclass, field
import hashlib

from app.services.billing.amounts import fail

MODEL_TYPES = ('TEXT', 'VISION', 'IMAGE', 'EMBEDDING', 'TTS', 'ASR', 'VIDEO')


def model_key(provider, model_id):
    return hashlib.sha256((provider.strip().lower() + '\0' + model_id).encode()).hexdigest()[:32]


@dataclass(frozen=True)
class UsageContract:
    real_metrics: tuple[str, ...] = ()
    raw_metrics: tuple[str, ...] = ()
    evidence_ref: str = ''

    def __post_init__(self):
        if (self.real_metrics or self.raw_metrics) and not self.evidence_ref:
            raise ValueError('Usage declarations require an adapter evidence reference')
        if not set(self.real_metrics) <= {'input_tokens', 'output_tokens'} or not set(self.raw_metrics) <= {'image_count', 'audio_milliseconds', 'character_count'}:
            raise ValueError('Unsupported usage declaration')


@dataclass
class ModelRegistration:
    provider: str
    model_id: str
    model_types: tuple[str, ...]
    configured: bool
    implemented: bool = True
    usage_contract: UsageContract = field(default_factory=UsageContract)
    provider_model_name: str | None = None

    def __post_init__(self):
        self.provider = self.provider.strip().lower()
        if not self.provider or len(self.provider) > 64 or not self.model_id or len(self.model_id) > 200:
            raise ValueError('Invalid registry identity')
        if not set(self.model_types) <= set(MODEL_TYPES):
            raise ValueError('Unknown model type')
        if self.usage_contract is None:
            self.usage_contract = UsageContract()

    @property
    def model_key(self):
        return model_key(self.provider, self.model_id)

    def public(self, pricing=None):
        return {'model_key': self.model_key, 'provider': self.provider, 'model_id': self.model_id,
                'provider_model_name': self.provider_model_name, 'model_types': sorted(self.model_types),
                'connection_status': 'NOT_IMPLEMENTED' if not self.implemented else 'NOT_TESTED' if self.configured else 'NOT_CONFIGURED',
                'configured': self.configured, 'supported_usage_modes':
                    (['REAL_TOKENS'] if self.usage_contract.real_metrics else []) + (['FOLDED_TOKENS'] if self.usage_contract.raw_metrics else []),
                'supported_raw_metrics': list(self.usage_contract.raw_metrics), 'pricing': pricing}


class ModelRegistry:
    def __init__(self, registrations=()):
        self.items = {}
        for item in registrations:
            self.register(item)

    def register(self, item):
        prior = self.items.get(item.model_key)
        if prior:
            if (prior.provider, prior.model_id) != (item.provider, item.model_id):
                raise ValueError('Model key collision')
            if prior.usage_contract != item.usage_contract:
                raise ValueError('Conflicting usage declarations for one model')
            prior.model_types = tuple(sorted(set(prior.model_types) | set(item.model_types)))
            prior.configured = prior.configured and item.configured
            prior.implemented = prior.implemented and item.implemented
        else:
            self.items[item.model_key] = item

    def get(self, key):
        if key not in self.items:
            fail('RESOURCE_NOT_FOUND', '模型不存在', 404)
        return self.items[key]

    @classmethod
    def from_settings(cls, settings):
        registry = cls()
        def add(provider, model, types, configured, implemented=True):
            if model:
                registry.register(ModelRegistration(provider, model, types, bool(configured), implemented))
        # Provider slots are fixed adapter identifiers, never URL-derived names.
        add('openai_compatible:llm', settings.llm_model, ('TEXT',), settings.llm_api_key and settings.llm_api_base)
        add('openai_compatible:llm', settings.llm_vision_model, ('VISION',), settings.llm_api_key and settings.llm_api_base)
        embedding_slot = 'embedding' if settings.embedding_base_url or settings.embedding_api_key else 'llm'
        add('openai_compatible:' + embedding_slot, settings.embedding_model, ('EMBEDDING',),
            (settings.embedding_api_key or settings.llm_api_key) and (settings.embedding_base_url or settings.llm_api_base))
        if settings.image_generation_provider == 'apilio':
            add('apilio:image', settings.apilio_image_model, ('IMAGE',), settings.apilio_image_api_key and settings.apilio_image_base_url)
        elif settings.image_generation_provider == 'ark_seedream':
            for model in (settings.image_generation_model, settings.image_product_model, settings.image_portrait_model, settings.image_edit_model, settings.image_upscale_model):
                add('ark_seedream:image', model, ('IMAGE',), (settings.image_generation_api_key or settings.llm_api_key) and (settings.image_generation_base_url or settings.llm_api_base))
        from app.services.llm import resolve_director_asr_provider
        asr_provider = resolve_director_asr_provider(settings)
        if asr_provider not in ('local', 'local_whisper'):
            providers = {'openai_compatible': 'openai_compatible:asr', 'volcengine': 'volcengine:asr', 'volcengine_asr': 'volcengine:asr', 'doubao_speech': 'volcengine:asr'}
            provider = providers.get(asr_provider)
            if provider:
                model = settings.director_asr_resource_id if provider == 'volcengine:asr' else settings.director_asr_model
                configured = settings.director_asr_api_key if provider == 'volcengine:asr' else settings.director_asr_api_key or settings.llm_api_key
                add(provider, model, ('ASR',), configured)
        add('doubao:tts', settings.doubao_tts_resource_id, ('TTS',), settings.doubao_tts_api_key)
        add('volcengine_ark:video', settings.ark_video_model, ('VIDEO',), settings.ark_api_key, False)
        return registry

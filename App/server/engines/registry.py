"""Versioned engine profiles and immutable workflow bindings."""
from dataclasses import dataclass
from pathlib import Path
import json
from portable_paths import from_server
PATHS = from_server()

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENGINE = 'qwen-edit-2511'
CANVASES = {'square': (1024, 1024), 'landscape': (1152, 768), 'portrait': (768, 1152)}
UPSCALE_MODEL = 'RealESRGAN_x2plus.pth'

@dataclass(frozen=True)
class Engine:
    id: str
    name: str
    native_prompt: bool
    quality_steps: dict
    sampler_node: str
    decode_node: str
    output_nodes: tuple
    prompt_node: str
    image_nodes: tuple
    workflow_version: str
    assets: tuple
    node_names: dict
    modes: tuple = ('single', 'reference', 'prompt')

    def workflow_path(self, mode):
        if self.native_prompt:
            return PATHS.engine_workflows / self.id / (mode + '.api.json')
        return PATHS.workflows / (('single' if mode == 'prompt' else mode) + '.api.json')

    def template(self, mode):
        return json.loads(self.workflow_path(mode).read_text(encoding='utf-8'))

ENGINES = {
    DEFAULT_ENGINE: Engine(
        DEFAULT_ENGINE, 'Qwen Image Edit 2511', False,
        {'low': 40, 'good': 50, 'high': 60}, '169', '158', ('9',), '151', ('41', '83'), '2511-v1',
        (('UNETLoader', 'unet_name', 'qwen_image_edit_2511_fp8mixed.safetensors'),
         ('CLIPLoader', 'clip_name', 'qwen_2.5_vl_7b_fp8_scaled.safetensors'),
         ('VAELoader', 'vae_name', 'qwen_image_vae.safetensors'),
         ('LoraLoaderModelOnly', 'lora_name', 'qwen-image-edit-plus-nsfw-lora.safetensors')),
        {'41':'Исходное изображение', '83':'Референс', '161':'Загрузка Qwen 2511',
         '162':'Текстовый энкодер', '146':'VAE', '170':'LoRA 2511', '145':'Sampling shift',
         '152':'CFG', '160':'Размер изображения', '151':'Промпт', '149':'Conditioning',
         '148':'Референсы', '147':'Conditioning', '156':'VAE encode',
         '169':'Sampling', '158':'VAE decode', '9':'Сохранение'}),
    'qwen-image-21-uc-fp8': Engine(
        'qwen-image-21-uc-fp8', 'Qwen Image 2.1 Uncensored FP8', True,
        {'low': 25, 'good': 35, 'high': 50}, '60', '70', ('90',), '40', ('11', '12'), '21-v1',
        (('UNETLoader', 'unet_name', 'qwen-image-2.1-UC-fp8.safetensors'),
         ('CLIPLoader', 'clip_name', 'qwen3vl_8b_int8_convrot.safetensors'),
         ('VAELoader', 'vae_name', 'qwen_image_2.1_vae_bf16.safetensors')),
        {'11':'Исходное изображение <image1>', '12':'Референс <image2>',
         '20':'Загрузка Qwen 2.1 FP8', '21':'Qwen3-VL INT8', '22':'VAE RGBA',
         '30':'KV cache', '40':'Conditioning Qwen 2.1', '50':'Размер latent',
         '60':'Sampling', '70':'VAE decode RGBA', '90':'Сохранение PNG'})
}

def get_engine(engine_id=None):
    engine_id = DEFAULT_ENGINE if engine_id is None else engine_id
    if not isinstance(engine_id, str) or engine_id not in ENGINES:
        raise ValueError('Неизвестный движок')
    return ENGINES[engine_id]

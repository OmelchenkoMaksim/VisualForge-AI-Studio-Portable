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
    # Template bindings that differ per engine: the text input name, the latent-size node of native prompt
    # graphs, and the SaveImage prefix used when Upscale rewires the output.
    prompt_key: str = 'prompt'
    size_node: str = '50'
    output_prefix: str = ''
    # Optional prompt expansion: the nodes that exist only to run the upsampler, the text encode they feed, and the preview
    # node whose text records the expanded caption. Without expansion the nodes are dropped and the prompt goes straight in.
    expand_nodes: tuple = ()
    encode_node: str = ''
    expansion_preview: str = ''
    # Native-prompt engines that need the user's instruction wrapped when two images are sent.
    reference_prefix: str = ''
    # Free video memory (MiB) to wait for before a job, so another GPU application holding several GB gets a clear refusal
    # instead of an out-of-memory error deep in ComfyUI. 0 = no wait (the Qwen engines rely on ComfyUI offloading).
    claim_vram_mb: int = 0

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
         '169':'Sampling', '158':'VAE decode', '9':'Сохранение'},
        output_prefix='Qwen_Edit_2511'),
    'qwen-image-21-uc-fp8': Engine(
        'qwen-image-21-uc-fp8', 'Qwen Image 2.1 Uncensored FP8', True,
        {'low': 25, 'good': 35, 'high': 50}, '60', '70', ('90',), '40', ('11', '12'), '21-v1',
        (('UNETLoader', 'unet_name', 'qwen-image-2.1-UC-fp8.safetensors'),
         ('CLIPLoader', 'clip_name', 'qwen3vl_8b_int8_convrot.safetensors'),
         ('VAELoader', 'vae_name', 'qwen_image_2.1_vae_bf16.safetensors')),
        {'11':'Исходное изображение <image1>', '12':'Референс <image2>',
         '20':'Загрузка Qwen 2.1 FP8', '21':'Qwen3-VL INT8', '22':'VAE RGBA',
         '30':'KV cache', '40':'Conditioning Qwen 2.1', '50':'Размер latent',
         '60':'Sampling', '70':'VAE decode RGBA', '90':'Сохранение PNG'},
        output_prefix='Qwen_Image_21_UC',
        reference_prefix='Edit <image1> as the source image; use <image2> as the reference. Follow the user instruction: '),
    # Text-to-image only: the graph has no image inputs, so single/reference are not modes of this engine.
    'z-image-turbo-int8': Engine(
        'z-image-turbo-int8', 'Z-Image Turbo INT8', True,
        {'low': 8}, '60', '70', ('90',), '40', (), 'zimage-turbo-v1',
        (('UNETLoader', 'unet_name', 'z_image_turbo_int8_convrot.safetensors'),
         ('CLIPLoader', 'clip_name', 'qwen_3_4b_fp8_mixed.safetensors'),
         ('VAELoader', 'vae_name', 'ae.safetensors')),
        {'20':'Загрузка Z-Image Turbo INT8', '21':'Текстовый энкодер Qwen3-4B', '22':'VAE',
         '25':'Sampling shift', '40':'Промпт', '41':'Нулевой negative', '50':'Размер latent',
         '60':'Sampling', '70':'VAE decode', '90':'Сохранение PNG'},
        modes=('prompt',), prompt_key='text', output_prefix='Z_Image_Turbo',
        # Measured on the RTX 5070 Ti (nvidia-smi, whole GPU): peak about 13.6 GB above idle at 1024x1024. ComfyUI takes what is
        # free, so the peak is not a minimum; the estimate stays below it to pass with the usual 1-2.5 GB desktop use.
        claim_vram_mb=11000),
    # The official workflow runs a Qwen3.5 prompt upsampler before the text encoder; short prompts give clearly worse results without it.
    'flux3-klein-int8': Engine(
        'flux3-klein-int8', 'FLUX.3 Klein INT8', True,
        {'low': 16}, '60', '70', ('90',), '25', ('10', '11'), 'flux3-klein-v1',
        (('UNETLoader', 'unet_name', 'flux3-image-klein-int8-convrot.safetensors'),
         ('CLIPLoader', 'clip_name', 'qwen3vl_4b_int8_convrot.safetensors'),
         ('CLIPLoader', 'clip_name', 'qwen3.5_9b_flux3_prompt_upsampler_int8_convrot.safetensors'),
         ('VAELoader', 'vae_name', 'flux2-vae.safetensors')),
        {'20':'Загрузка FLUX.3 Klein INT8', '21':'Текстовый энкодер Qwen3-VL-4B', '22':'VAE', '23':'Загрузка upsampler Qwen3.5',
         '24':'Системный промпт upsampler', '25':'Промпт', '26':'Расширение промпта', '27':'Расширенный промпт',
         '10':'Исходное изображение', '11':'Референс', '28':'Изображения для upsampler', '42':'Нулевой negative', '43':'Референсы (latent)',
         '40':'Conditioning', '41':'Пустой negative', '50':'Размер latent', '60':'Sampling', '70':'VAE decode', '90':'Сохранение PNG'},
        prompt_key='value', output_prefix='FLUX3_Klein',
        expand_nodes=('23', '24', '25', '26', '28'), encode_node='40', expansion_preview='27',
        # Measured peaks above idle: about 13.9 GB for a prompt with the upsampler, 14.6-14.7 GB for Single/Reference. An estimate
        # below the peak, for the same reason as Z-Image; the peaks leave no room for a second GPU application.
        claim_vram_mb=12000)
}

def get_engine(engine_id=None):
    engine_id = DEFAULT_ENGINE if engine_id is None else engine_id
    if not isinstance(engine_id, str) or engine_id not in ENGINES:
        raise ValueError('Неизвестный движок')
    return ENGINES[engine_id]

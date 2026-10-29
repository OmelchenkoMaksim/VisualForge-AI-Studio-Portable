"""Versioned engine profiles and immutable workflow bindings."""
from ui_strings import source as ui_text
from dataclasses import dataclass, field
from pathlib import Path
import json
from portable_paths import from_server
PATHS = from_server()

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENGINE = 'qwen-edit-2511'
WORKFLOW_PREFIXES = {
    DEFAULT_ENGINE: 'qwen2511',
    'qwen-image-21-uc-fp8': 'qwen21',
    'flux3-klein-int8': 'flux3',
    'z-image-turbo-int8': 'zimage',
}
CANVASES = {'square': (1024, 1024), 'landscape': (1152, 768), 'portrait': (768, 1152)}
UPSCALE_MODEL = 'RealESRGAN_x2plus.pth'
ZIMAGE_UNION = 'Z-Image-Turbo-Fun-Controlnet-Union-2.1-lite-2602-8steps.safetensors'
ZIMAGE_TILE = 'Z-Image-Turbo-Fun-Controlnet-Tile-2.1-lite-2601-8steps.safetensors'
QWEN_LIGHTNING = 'Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors'
QWEN_UNCENSORED = 'qwen-image-edit-plus-nsfw-lora.safetensors'
QWEN21_PE_T2I = 'qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors'
QWEN21_PE_I2I = 'qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors'

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
    # Optional workflows must not disable text generation when their extra nodes or weights are absent.
    optional_modes: tuple = ()
    mode_quality_steps: dict = field(default_factory=dict)
    mode_components: dict = field(default_factory=dict)
    upscale_modes: tuple = ()
    text_modes: tuple = ('prompt',)
    max_reference_images: int = 2
    native_megapixels: tuple = (1,)
    native_claim_vram_mb: dict = field(default_factory=dict)
    quality_assets: dict = field(default_factory=dict)
    quality_components: dict = field(default_factory=dict)
    # Optional, mode-specific prompt enhancers must not become base-engine requirements.
    expansion_assets: dict = field(default_factory=dict)

    def required_vram(self, mode, megapixels=1):
        return self.native_claim_vram_mb.get(megapixels, self.claim_vram_mb) if mode in self.text_modes else self.claim_vram_mb

    def steps(self, mode, quality):
        return self.mode_quality_steps.get(mode, self.quality_steps)[quality]

    def workflow_path(self, mode):
        # Filenames identify the model even when copied outside their workflow directory.
        prefix = WORKFLOW_PREFIXES[self.id]
        if self.native_prompt:
            return PATHS.engine_workflows / self.id / (prefix + '-' + mode + '.api.json')
        return PATHS.workflows / (prefix + '-' + ('single' if mode == 'prompt' else mode) + '.api.json')

    def template(self, mode):
        return json.loads(self.workflow_path(mode).read_text(encoding='utf-8'))

ENGINES = {
    # The 2511 templates resize by area: fixed Kontext aspect buckets crop panoramic edges before conditioning.
    DEFAULT_ENGINE: Engine(
        DEFAULT_ENGINE, 'Qwen Image Edit 2511', False,
        {'low': 40, 'fast': 4}, '169', '158', ('9',), '151', ('41', '83'), '2511-v2',
        (('UNETLoader', 'unet_name', 'qwen_image_edit_2511_fp8mixed.safetensors'),
         ('CLIPLoader', 'clip_name', 'qwen_2.5_vl_7b_fp8_scaled.safetensors'),
         ('VAELoader', 'vae_name', 'qwen_image_vae.safetensors')),
        {'41':ui_text('image.workflow_stage_source_image'), '83':ui_text('image.source_reference_image_label'), '161':ui_text('image.workflow_stage_loading_qwen_2511'),
         '162':ui_text('image.workflow_stage_text_encoder'), '146':'VAE', '170':'LoRA 2511', '145':'Sampling shift',
         '152':'CFG', '160':ui_text('image.workflow_stage_image_size'), '151':ui_text('image.workflow_stage_prompt_processing_label'), '149':'Conditioning',
         '148':ui_text('image.workflow_stage_references'), '147':'Conditioning', '156':'VAE encode',
         '169':'Sampling', '158':'VAE decode', '9':ui_text('image.workflow_stage_saving')},
        output_prefix='Qwen_Edit_2511', modes=('single', 'reference', 'prompt', 'multi'), optional_modes=('multi',), max_reference_images=3,
        quality_assets={'fast':(('LoraLoaderModelOnly', 'lora_name', QWEN_LIGHTNING),)},
        quality_components={'fast':'image.model.qwen-edit-lightning'}),
    'qwen-image-21-uc-fp8': Engine(
        'qwen-image-21-uc-fp8', 'Qwen Image 2.1 Uncensored FP8', True,
        {'low': 25, 'good': 40}, '60', '70', ('90',), '40', ('11', '12'), '21-v2',
        (('UNETLoader', 'unet_name', 'qwen-image-2.1-UC-fp8.safetensors'),
         ('CLIPLoader', 'clip_name', 'qwen3vl_8b_int8_convrot.safetensors'),
         ('VAELoader', 'vae_name', 'qwen_image_2.1_vae_bf16.safetensors')),
        {'11':ui_text('image.workflow_stage_source_image_image1'), '12':ui_text('image.workflow_stage_reference_image2'),
         '20':ui_text('image.workflow_stage_loading_qwen_2_1_fp8'), '21':'Qwen3-VL INT8', '22':'VAE RGBA',
         '30':'KV cache', '40':'Conditioning Qwen 2.1', '50':ui_text('image.workflow_stage_latent_dimensions'),
         '60':'Sampling', '70':'VAE decode RGBA', '90':ui_text('image.workflow_stage_saving_png')},
        output_prefix='Qwen_Image_21_UC', modes=('single', 'reference', 'prompt', 'multi'), optional_modes=('multi',),
        max_reference_images=10, native_megapixels=(1, 4),
        expansion_assets={'prompt':QWEN21_PE_T2I, 'single':QWEN21_PE_I2I,
                          'reference':QWEN21_PE_I2I, 'multi':QWEN21_PE_I2I},
        reference_prefix='Edit <image1> as the source image; use <image2> as the reference. Follow the user instruction: '),
    # Turbo alone is not an instruction editor; image modes use low-denoise refinement or separate Union/Tile patches.
    'z-image-turbo-int8': Engine(
        'z-image-turbo-int8', 'Z-Image Turbo INT8', True,
        {'low': 8}, '60', '70', ('90',), '40', ('10', '12'), 'zimage-turbo-v2',
        (('UNETLoader', 'unet_name', 'z_image_turbo_int8_convrot.safetensors'),
         ('CLIPLoader', 'clip_name', 'qwen_3_4b_fp8_mixed.safetensors'),
         ('VAELoader', 'vae_name', 'ae.safetensors')),
        {'20':ui_text('image.workflow_stage_loading_z_image_turbo_int8'), '21':ui_text('image.workflow_stage_qwen3_4_b_text_encoder'), '22':'VAE',
         '25':'Sampling shift', '40':ui_text('image.workflow_stage_prompt_processing_label'), '41':ui_text('image.workflow_stage_zero_negative_conditioning'), '50':ui_text('image.workflow_stage_latent_dimensions'),
         '60':'Sampling', '70':'VAE decode', '90':ui_text('image.workflow_stage_saving_png')},
        modes=('prompt', 'detail', 'control', 'inpaint', 'tile'), prompt_key='text', output_prefix='Z_Image_Turbo',
        optional_modes=('detail', 'control', 'inpaint', 'tile'), mode_quality_steps={'detail': {'low': 5}},
        upscale_modes=('detail', 'tile'),
        mode_components={'detail': ('image.model.upscale',),
                         'control': ('image.model.z-image-controlnet-union',),
                         'inpaint': ('image.model.z-image-controlnet-union',),
                         'tile': ('image.model.z-image-controlnet-tile', 'image.model.upscale')},
        # Measured on the RTX 5070 Ti (nvidia-smi, whole GPU): peak about 13.6 GB above idle at 1024x1024. ComfyUI takes what is
        # free, so the peak is not a minimum; the estimate stays below it to pass with the usual 1-2.5 GB desktop use.
        claim_vram_mb=11000),
    # The official workflow expands short requests to full scene captions before text encoding.
    'flux3-klein-int8': Engine(
        'flux3-klein-int8', 'FLUX.3 Klein INT8', True,
        {'low': 16}, '60', '70', ('90',), '25', ('10', '11'), 'flux3-klein-v2',
        (('UNETLoader', 'unet_name', 'flux3-image-klein-int8-convrot.safetensors'),
         ('CLIPLoader', 'clip_name', 'qwen3vl_4b_int8_convrot.safetensors'),
         ('CLIPLoader', 'clip_name', 'qwen3.5_9b_flux3_prompt_upsampler_int8_convrot.safetensors'),
         ('VAELoader', 'vae_name', 'flux2-vae.safetensors')),
        {'20':ui_text('image.workflow_stage_loading_flux_3_klein_int8'), '21':ui_text('image.workflow_stage_qwen3_vl_4_b_text_encoder'), '22':'VAE', '23':ui_text('image.workflow_stage_loading_qwen3_5_upsampler'),
         '24':ui_text('image.workflow_stage_upsampler_system_prompt'), '25':ui_text('image.workflow_stage_prompt_processing_label'), '26':ui_text('image.workflow_stage_prompt_expansion'), '27':ui_text('image.workflow_stage_expanded_prompt'),
         '10':ui_text('image.workflow_stage_source_image'), '11':ui_text('image.source_reference_image_label'), '28':ui_text('image.workflow_stage_upsampler_images'), '42':ui_text('image.workflow_stage_zero_negative_conditioning'), '43':ui_text('image.workflow_stage_references_latent'),
         '40':'Conditioning', '41':ui_text('image.workflow_stage_empty_negative_prompt'), '50':ui_text('image.workflow_stage_latent_dimensions'), '60':'Sampling', '70':'VAE decode', '90':ui_text('image.workflow_stage_saving_png')},
        prompt_key='value', output_prefix='FLUX3_Klein',
        modes=('single', 'reference', 'prompt', 'multi', 'layout'), optional_modes=('layout',),
        text_modes=('prompt', 'layout'), max_reference_images=10, native_megapixels=(1, 4, 16),
        # A real 4096-square probe reached 15924 MiB whole-card; reject occupied GPUs before entering this slow profile.
        native_claim_vram_mb={16:15000},
        expand_nodes=('23', '24', '25', '26', '28'), encode_node='40', expansion_preview='27',
        # Measured peaks above idle: about 13.9 GB for a prompt with the upsampler, 14.6-14.7 GB for Single/Reference. An estimate
        # below the peak, for the same reason as Z-Image; the peaks leave no room for a second GPU application.
        claim_vram_mb=12000)
}

def get_engine(engine_id=None):
    engine_id = DEFAULT_ENGINE if engine_id is None else engine_id
    if not isinstance(engine_id, str) or engine_id not in ENGINES:
        raise ValueError(ui_text('image.get_engine_unknown'))
    return ENGINES[engine_id]

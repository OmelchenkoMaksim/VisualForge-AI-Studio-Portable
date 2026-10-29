"""Read-only model facts and catalog-backed storage budgets; never downloads or hashes weights."""
from ui_strings import source as ui_text
from pathlib import Path
from provision import component_catalog as catalog_module
from provision.install_engine import Engine as Installer, SPACE_MARGIN

COMPONENTS = {
    'image:qwen-edit-2511': 'image.model.qwen-edit-2511',
    'image:qwen-image-21-uc-fp8': 'image.model.qwen-image-21-uc',
    'image:z-image-turbo-int8': 'image.model.z-image-turbo',
    'image:flux3-klein-int8': 'image.model.flux3-klein',
    'video:freevideo-h3-int8': 'video.model.h3',
    'video:wan22-ti2v-5b': 'video.model.wan-ti2v-5b',
    'video:wan22-s2v-14b': 'video.model.wan-s2v-14b',
    'video:ltx25-distilled': 'video.model.ltx25',
    'music:ace-step-15-turbo': 'music.model.ace-step',
    'music:minimax-music3-int8': 'music.model.minimax-music3',
    'music:stable-audio-3-medium': 'music.model.stable-audio-3',
}
MODES = dict(t2v=ui_text('hardware.model_requirements_video_text'), i2v=ui_text('hardware.model_requirements_image_animation'), first_last=ui_text('hardware.model_requirements_first_last_frame'),
    characters=ui_text('hardware.model_requirements_characters_references'), s2v=ui_text('hardware.model_requirements_speech_video'), instrumental=ui_text('hardware.generation_instrumental_mode_label'), song=ui_text('hardware.model_requirements_song_vocals'),
    single=ui_text('hardware.model_requirements_editing'), reference=ui_text('hardware.model_requirements_editing_reference'), prompt=ui_text('hardware.model_requirements_image_text'),
    audio=ui_text('hardware.model_requirements_music_sounds_description'), detail=ui_text('hardware.model_requirements_image_detail_mode'),
    control=ui_text('hardware.model_requirements_image_control_mode'), inpaint=ui_text('hardware.model_requirements_image_inpaint_mode'), tile=ui_text('hardware.model_requirements_image_tile_mode'),
    multi=ui_text('image.flux_multi_mode_label'), layout=ui_text('image.flux_layout_mode_label'))


def facts(module, ident, model=None):
    if module == 'image':
        from engines.image_registry import ENGINES, CANVASES
        engine = ENGINES[ident]
        weights = {'qwen-image-21-uc-fp8': ui_text('hardware.model_requirements_characteristics_fp8_int8_text_encoder'),
                   'qwen-edit-2511': ui_text('hardware.qwen_2511_weights'),
                   'z-image-turbo-int8': ui_text('hardware.model_requirements_characteristics_int8_transformer_fp8_qwen3_4_b'),
                   'flux3-klein-int8': ui_text('hardware.model_requirements_characteristics_distilled_int8_qwen3_vl_4_b')}.get(ident, ui_text('hardware.model_requirements_characteristics_fp8_text_encoder'))
        modes = {**MODES, 'multi':ui_text('image.qwen_multi_mode_label')} if ident == 'qwen-edit-2511' else MODES
        rows = [(ui_text('hardware.model_requirements_characteristics_capabilities'), ', '.join(modes[m] + (ui_text('hardware.model_requirements_characteristics_experimental') if m == 'prompt' and not engine.native_prompt else '') for m in engine.modes)), (ui_text('hardware.model_requirements_characteristics_output_format'), 'PNG'),
            (ui_text('hardware.model_requirements_characteristics_quality'), ' / '.join(str(n) for n in dict.fromkeys([*engine.quality_steps.values(), *(n for presets in engine.mode_quality_steps.values() for n in presets.values())])) + ui_text('hardware.model_requirements_characteristics_steps')),
            (ui_text('hardware.model_requirements_characteristics_frame_scratch'), ui_text('hardware.model_requirements_flux_native_sizes') if ident == 'flux3-klein-int8' else ui_text('hardware.qwen21_canvas_sizes') if ident == 'qwen-image-21-uc-fp8' else ui_text('hardware.qwen_2511_canvas_sizes') if ident == 'qwen-edit-2511' else ', '.join('%d×%d' % size for size in dict.fromkeys(CANVASES.values()))),
            (ui_text('hardware.model_requirements_characteristics_weights'), weights)]
        if 'single' not in engine.modes:
            rows.append((ui_text('hardware.model_requirements_characteristics_features'), ui_text('hardware.model_requirements_zimage_optional_modes_note')))
        if ident == 'flux3-klein-int8':
            rows.append((ui_text('hardware.model_license_row_label'), ui_text('hardware.model_requirements_characteristics_flux_kommunity_license_noncommercial_use_commercial')))
        if ident == 'qwen-edit-2511':
            rows.append((ui_text('hardware.qwen_2511_profiles_label'), ui_text('image.qwen_quality_explanation')))
        return rows
    if module == 'video':
        from video.video_registry import ENGINES
        engine = ENGINES[ident]
        return [(ui_text('hardware.model_requirements_characteristics_capabilities'), ', '.join(MODES[m] for m in engine['modes'])),
            (ui_text('hardware.model_requirements_characteristics_profile'), engine['release']),
            (ui_text('hardware.model_requirements_characteristics_frame_dimensions'), ', '.join('%d×%d' % tuple(size) for size in engine['resolutions'])),
            (ui_text('hardware.model_requirements_characteristics_frame_rate'), '%g FPS' % engine['fps']),
            (ui_text('hardware.model_requirements_characteristics_durations'), ' · '.join(('%g' % round(n / engine['fps'], 3)).replace('.', ',') for n in engine['frames']) + ui_text('hardware.duration_seconds_unit_suffix')),
            (ui_text('hardware.model_requirements_characteristics_audio'), ui_text('hardware.model_requirements_characteristics_joint_video_audio_generation') if ident == 'ltx25-distilled' else ui_text('hardware.model_requirements_characteristics_speech_synchronization') if ident == 'wan22-s2v-14b' else ui_text('hardware.model_requirements_characteristics_no_audio_generation') if ident == 'wan22-ti2v-5b' else ui_text('hardware.model_requirements_characteristics_video_audio_voice_cloning_separately')),
            (ui_text('hardware.model_requirements_characteristics_features'), engine['limitations'])]
    if module == 'music':
        from music.music_registry import ENGINES
        engine = ENGINES[ident]
        return [(ui_text('hardware.model_requirements_characteristics_capabilities'), ', '.join(MODES[m] for m in engine['modes'])),
            (ui_text('hardware.generation_identity_duration_field_label'), ui_text('hardware.model_requirements_characteristics_s') % (engine['min_duration'], engine['max_duration'])),
            (ui_text('hardware.generation_output_region_label'), ui_text('hardware.model_requirements_characteristics_k_hz') % (engine['format'].upper(), engine['sample_rate'] / 1000)),
            (ui_text('hardware.model_license_row_label'), engine['license']),
            (ui_text('hardware.model_requirements_characteristics_planner'), engine['planner'] or ui_text('hardware.model_requirements_characteristics_not_used')),
            (ui_text('hardware.model_requirements_characteristics_features'), engine.get('limitations', ui_text('hardware.model_requirements_characteristics_local_instrumental_music_song_generation_no')))]
    model = model or {}
    return [(ui_text('hardware.model_requirements_characteristics_capabilities'), ui_text('hardware.model_requirements_characteristics_text_images') if model.get('vision') else ui_text('hardware.text_section_title_label')),
        (ui_text('hardware.model_requirements_characteristics_parameters'), model.get('parameter_size') or ui_text('hardware.specifications_missing_value_label')),
        (ui_text('hardware.model_requirements_characteristics_quantization'), model.get('quantization') or ui_text('hardware.specifications_missing_value_label')),
        (ui_text('hardware.model_requirements_characteristics_build_context'), ui_text('hardware.model_requirements_characteristics_tokens') % model['parameters']['num_ctx'] if model.get('parameters', {}).get('num_ctx') else ui_text('hardware.model_requirements_characteristics_ollama_settings')),
        (ui_text('hardware.model_requirements_characteristics_execution'), ui_text('hardware.model_requirements_characteristics_ollama_gpu_cpu_ram'))]


def storage(installer, statuses, ident):
    order = catalog_module.closure(installer.catalog, [ident])
    models = [installer.by_id[key] for key in order if installer.by_id[key]['kind'] == 'model']
    software = [installer.by_id[key] for key in order if installer.by_id[key]['kind'] == 'software']
    # Shared encoders/models occur once in a dependency closure. File paths are deduplicated too.
    files = {item['path']: item for component in models for item in component.get('files', [])}
    model_bytes = sum(item['bytes'] for item in files.values())
    runtime_download = sum(catalog_module.download_bytes(component) for component in software)
    runtime_disk = sum(max(component.get('disk_bytes', 0), catalog_module.download_bytes(component)) for component in software)
    missing = [key for key in order if statuses[key]['state'] != 'installed']
    remaining = sum(statuses[key]['remaining_bytes'] for key in missing)
    additional = int(sum(installer._disk_need(installer.by_id[key], statuses[key]) for key in missing) * SPACE_MARGIN)
    # Cold installs need both unpacked software and its download cache, plus the installer reserve.
    # Keep full-copy budgets separate from what this installation still needs to download.
    return dict(
        source='catalog',
        model_bytes=model_bytes,
        model_download_bytes=model_bytes,
        full_download_bytes=model_bytes + runtime_download,
        runtime_disk_bytes=runtime_disk,
        clean_install_free_bytes=int((model_bytes + runtime_disk + runtime_download) * SPACE_MARGIN),
        remaining_download_bytes=remaining,
        additional_free_bytes=additional,
        installed=not missing,
        shared_model_components=[component['title'] for component in models if component['id'] != ident],
    )


def enrich(profiles, text_models=(), root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    catalog_path = root/'App/config/component-catalog.json'
    try:
        installer = Installer(root, catalog_path=catalog_path) if catalog_path.is_file() else None
    except (OSError, ValueError, catalog_module.CatalogError):
        installer = None
    # Read relevant components only; do not stat every optional model in Components.
    keys = set()
    statuses = {}
    if installer:
        for key in COMPONENTS.values():
            # A component can be absent while its gated download is not pinned yet; its card then shows unknown storage.
            if key in installer.by_id:
                keys.update(catalog_module.closure(installer.catalog, [key]))
        statuses = {key: installer.component_status(installer.by_id[key]) for key in keys}
    texts = {model['name']: model for model in text_models}
    for key, profile in profiles.items():
        model = texts.get(profile['id'], {})
        profile['characteristics'] = facts(profile['module'], profile['id'], model)
        if key in COMPONENTS and installer and COMPONENTS[key] in installer.by_id:
            profile['storage'] = storage(installer, statuses, COMPONENTS[key])
            optional = 'image.model.upscale' if profile['module'] == 'image' else 'video.model.qwen3-tts' if profile['id'] in ('freevideo-h3-int8', 'wan22-s2v-14b') else None
            if optional:
                component = installer.by_id[optional]
                profile['optional_download'] = dict(name=component['title'], bytes=catalog_module.download_bytes(component))
        elif profile['module'] == 'text':
            profile['storage'] = dict(source='local_ollama', model_bytes=model.get('disk_size', model.get('size')),
                model_download_bytes=None, full_download_bytes=None, runtime_disk_bytes=installer.by_id['text.runtime'].get('disk_bytes') if installer else None,
                clean_install_free_bytes=None, remaining_download_bytes=0, additional_free_bytes=0, installed=True,
                shared_model_components=[])
        else:
            profile['storage'] = dict(source='unknown', model_bytes=None)
    return profiles

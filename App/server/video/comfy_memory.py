"""Model unloading shared by the native-ComfyUI video workers."""
import gc


def release_models(memory, torch):
    """Unload every model. A model that sits partly on the card goes to host memory in 1 GB steps, returning the card's memory after each.

    `--disable-smart-memory` makes every unload a full one, and a single `.to(cpu)` of a partly loaded 14-22B transformer copies the
    ~12 GB that sit on the card into fresh host tensors while the card's cached blocks stay allocated. On Windows the card's memory is
    charged against the same commit limit as host memory, so the job needed ~12 GB of commit twice at once. Measured on 2026-10-31
    (62 GB RAM, a normal desktop session): loading the LTX transformer charged the process ~39 GB, the system had ~11 GB left, the copy
    ran it out, and the failed host allocation surfaced as an access violation in c10.dll (frame: comfy_kitchen `_handle_to`, called
    from ModelPatcher.unpatch_model) that killed the job. Stepping with `empty_cache` keeps the total flat.
    """
    for loaded in memory.current_loaded_models:
        patcher = loaded.model
        while patcher.loaded_size() > 0 and patcher.partially_unload(patcher.offload_device, 1024 ** 3) > 0:
            torch.cuda.empty_cache()
    memory.unload_all_models()
    gc.collect()
    memory.soft_empty_cache()

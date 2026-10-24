"""Model-table functions declared in ``data/functions.contract``."""


def gen_codex_model_table(provider_name, models):
    """Map each model id to its provider and remote id without retaining borrows."""
    return {
        model: {"provider": provider_name, "remote_model_id": model}
        for model in models
    }

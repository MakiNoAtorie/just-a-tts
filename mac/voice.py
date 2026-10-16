"""The voice model on Apple silicon, through MLX."""

import mlx.core as mx
from mlx_audio.tts.utils import load_model as load  # noqa: F401  (load is part of what the engine calls)

seed = mx.random.seed
free = mx.clear_cache

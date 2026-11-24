"""Hearthside's native plugin entrypoint."""
from pathlib import Path

def register(ctx):
    ctx.register_skill('hearthside', Path(__file__).parent / 'skills/hearthside/SKILL.md')

"""Compatibility module; both entry points share the exact provider contract."""
import sys
import model_api
sys.modules[__name__]=model_api

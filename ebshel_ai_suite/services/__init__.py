# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Framework services.

Services are plain Python classes that receive an Odoo environment. They hold
the behaviour of the suite (engine calls, context building, capability
execution, retrieval, ...) while models stay thin configuration holders.
"""
from . import guardrails
from . import engines
from . import usage_guard
from . import llm_gateway
from . import context_builder
from . import prompt_renderer
from . import domain_guard
from . import capability_schema
from . import value_converter
from . import capability_handlers
from . import capability_runner
from . import extensions
from . import retrieval_engine
from . import nl_search
from . import conversation_runner
from . import text_tools
from . import field_generator
from . import automation_engine
from . import template_prompts
from . import decision_engine
from . import mcp_server

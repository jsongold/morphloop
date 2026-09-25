"""Tests for the dev-stack fake LLM provider (#130).

The self-check that matters: for every bundled schema a real pack or the SDK's
own contracts actually asks an LLM to answer, the fake's synthesized output
validates. That is the property `FakeDevLLMProvider` exists to guarantee --
not that it reproduces any particular content.
"""

from __future__ import annotations

import jsonschema
import pytest

from harness.adapters.fake_llm import FakeDevLLMProvider, FakeLLMProviderError, _minimal_instance
from harness.core.contract_schemas import ContractSchemas
from harness.core.ports import LLMMessage, LLMProvenance, LLMRequest
from harness.core.ports.llm import LLMTool, LLMToolRequest

PROVENANCE = LLMProvenance(
    provider="fake",
    model="fake/fake",
    prompt_id="p",
    prompt_version="1",
    generation_parameters={},
)

# Every schema an LLM output is actually validated against in this repo: the
# five `contracts/schemas/llm/*` output schemas, plus the pack v2 content
# schemas the generator (`harness/core/generator/runtime.py`) asks for.
SCHEMA_IDS = [
    "schemas/llm/tutor.reply/1.json",
    "schemas/llm/evaluator.judgment/1.json",
    "schemas/llm/learner_model.update/1.json",
    "schemas/llm/memo_summarizer.note/1.json",
    "schemas/llm/generator.activity_candidate/1.json",
    "schemas/pack/v2/drill-item.json",
    "schemas/pack/v2/textbook-doc.json",
]


@pytest.fixture(scope="module")
def schemas() -> ContractSchemas:
    return ContractSchemas.load()


@pytest.mark.parametrize("relative_path", SCHEMA_IDS)
def test_complete_structured_satisfies_every_bundled_schema(
    schemas: ContractSchemas, relative_path: str
) -> None:
    schema_id = ContractSchemas.id_for(relative_path)
    schema = schemas.bundle(schema_id)
    request = LLMRequest(
        role="generator",
        llm=PROVENANCE,
        messages=(LLMMessage(role="user", content="go"),),
        output_schema_id=schema_id,
        output_schema=schema,
    )
    response = FakeDevLLMProvider().complete_structured(request)
    jsonschema.Draft202012Validator(schema).validate(response.output)
    assert response.provenance.provider == "fake"


def test_complete_structured_validates_before_returning() -> None:
    # `required` naming a property with no matching entry in `properties` (legal
    # JSON Schema, just not how contracts/ schemas are written): the generator
    # has nothing to fill it with, so the defense-in-depth validation must catch
    # the resulting instance rather than hand back something invalid.
    schema = {"type": "object", "required": ["missing"]}
    with pytest.raises(FakeLLMProviderError):
        FakeDevLLMProvider().complete_structured(
            LLMRequest(
                role="generator",
                llm=PROVENANCE,
                messages=(LLMMessage(role="user", content="go"),),
                output_schema_id="urn:test",
                output_schema=schema,
            )
        )


def test_complete_with_tools_returns_text_and_no_tool_calls() -> None:
    tool = LLMTool(name="noop", description="d", parameters={"type": "object"})
    response = FakeDevLLMProvider().complete_with_tools(
        LLMToolRequest(
            llm=PROVENANCE,
            messages=(LLMMessage(role="user", content="hi"),),
            tools=(tool,),
            tool_choice="auto",
        )
    )
    assert response.content
    assert response.tool_calls == ()
    assert response.provenance.provider == "fake"


def test_minimal_instance_prefers_a_null_anyof_branch() -> None:
    schema = {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]}
    assert _minimal_instance(schema) is None


def test_minimal_instance_falls_back_to_the_first_anyof_branch() -> None:
    schema = {"anyOf": [{"type": "string", "minLength": 1}, {"type": "integer"}]}
    assert _minimal_instance(schema) == "fake"


def test_drill_output_gets_a_null_lab(schemas: ContractSchemas) -> None:
    # The generator's drill output schema (PreGenerator.output_schema) makes `lab`
    # a required-but-nullable candidate. The minimal instance must take the null
    # branch: a synthesized lab candidate comes with an item whose `answer_mode`
    # is 'text', which the runtime rejects ("a lab variant needs an 'artifact'
    # item"), so fake-backed drill generation would never complete.
    schema = {
        "type": "object",
        "required": ["item", "lab"],
        "properties": {
            "item": schemas.bundle(ContractSchemas.id_for("schemas/pack/v2/drill-item.json")),
            "lab": {
                "anyOf": [
                    schemas.bundle(
                        ContractSchemas.id_for("schemas/llm/generator.activity_candidate/1.json")
                    ),
                    {"type": "null"},
                ]
            },
        },
        "additionalProperties": False,
    }
    request = LLMRequest(
        role="generator",
        llm=PROVENANCE,
        messages=(LLMMessage(role="user", content="go"),),
        output_schema_id="urn:test",
        output_schema=schema,
    )
    response = FakeDevLLMProvider().complete_structured(request)
    jsonschema.Draft202012Validator(schema).validate(response.output)
    assert response.output["lab"] is None

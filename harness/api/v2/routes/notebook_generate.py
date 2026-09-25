"""`POST /v2/notebook/generate`: start pre-generation in the background (#34, #64).

The request is accepted at once (202) and the work runs after the response in
a FastAPI background task; `PreGenerator.generate` is synchronous, so Starlette
runs it in its thread pool. No queue service. Nothing is returned for the
generated content: it appears in `generated_documents` only once it has passed
validation, and a rejected candidate never appears.

Wiring: the v2 pack (`PackV2Dep`, its `generator` LLM role), the generated
documents store (`GeneratedDocumentsDep`) and the litellm provider
(`app.state.llm`, built on first use). Lab variants are not wired: pack v2
declares no solution-step timeout for a lab, so the generator here only
references existing pack labs; a lab candidate is rejected.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from harness.adapters.litellm import LiteLLMProvider
from harness.api.v2.deps import GeneratedDocumentsDep, PackV2Dep
from harness.api.v2.models import V2Model
from harness.core.contract_schemas import ContractSchemas
from harness.core.generator.runtime import GeneratedResource, GenerateRequest, PreGenerator
from harness.core.ports import LLMProvider

router = APIRouter(tags=["notebook"])


class GenerateBody(V2Model):
    resource: GeneratedResource
    memo_entries: list[dict[str, Any]] = []
    gap: dict[str, Any] | None = None


def llm_of(request: Request) -> LLMProvider:
    """The LLM provider; litellm, built and cached on `app.state` on first use."""
    llm: LLMProvider | None = getattr(request.app.state, "llm", None)
    if llm is None:
        llm = LiteLLMProvider()
        request.app.state.llm = llm
    return llm


def pregenerator_of(
    request: Request,
    pack: PackV2Dep,
    store: GeneratedDocumentsDep,
    llm: Annotated[LLMProvider, Depends(llm_of)],
) -> PreGenerator:
    """The wired `PreGenerator`, cached on `app.state` on first use."""
    generator: PreGenerator | None = getattr(request.app.state, "pregenerator", None)
    if generator is None:
        try:
            generator = PreGenerator(
                pack=pack,
                llm=llm,
                provider="litellm",
                schemas=ContractSchemas.load(),
                store=store,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        request.app.state.pregenerator = generator
    return generator


@router.post("/notebook/generate", status_code=status.HTTP_202_ACCEPTED)
def generate(
    body: GenerateBody,
    background: BackgroundTasks,
    generator: Annotated[PreGenerator, Depends(pregenerator_of)],
) -> dict[str, str]:
    background.add_task(
        generator.generate,
        GenerateRequest(resource=body.resource, memo_entries=body.memo_entries, gap=body.gap),
    )
    return {"status": "accepted"}

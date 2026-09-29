# Integration Guide: AWS Bedrock Converse

Use Agent Skill Assay skills with models hosted on **Amazon Bedrock** via the **Converse** API and native tool-use schema.

## Install

```bash
pip install "agent-skill-assay[bedrock]"
pip install "agent-skill-assay[monitoring_business_diagnostic]"   # example skill extra
```

Installs [`boto3`](https://pypi.org/project/boto3/). Configure AWS credentials (IAM role on EC2/ECS/Lambda, or `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` locally) and set `AWS_REGION`.

## Adapter

```python
from skill_assay.core.loader import SkillLoader

bundle = SkillLoader.load_skill("monitoring/business_diagnostic")
tool = SkillLoader.to_bedrock_tool(bundle)
# tool → {"toolSpec": {"name", "description", "inputSchema": {"json": ...}}}
```

Pass `tool` inside `toolConfig.tools` when calling `bedrock-runtime` `converse()`. Tool names use the same sanitization as OpenAI adapters (`monitoring/kpi_gate` -> `monitoring_kpi_gate`).

Claude, Llama, and other models on Bedrock share this Converse tool shape — you do not need a separate adapter per Bedrock model family.

## Minimal loop

```python
import os

import boto3

from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()

bundle = SkillLoader.load_skill("monitoring/business_diagnostic")
skill = bundle["class"]()
tool = SkillLoader.to_bedrock_tool(bundle)
tool_name = tool["toolSpec"]["name"]

client = boto3.client("bedrock-runtime", region_name=os.environ["AWS_REGION"])
model_id = os.environ["BEDROCK_MODEL_ID"]

messages = [
    {
        "role": "user",
        "content": [{"text": "Check whether https://example.com/docs allows crawling."}],
    }
]

response = client.converse(
    modelId=model_id,
    system=[{"text": bundle["instructions"]}],
    messages=messages,
    toolConfig={"tools": [tool], "toolChoice": {"auto": {}}},
)

while True:
    output = response["output"]["message"]
    messages.append(output)
    tool_uses = [block for block in output["content"] if "toolUse" in block]
    if not tool_uses:
        break

    tool_results = []
    for block in tool_uses:
        use = block["toolUse"]
        if use["name"] != tool_name:
            raise RuntimeError(f"Unexpected tool: {use['name']}")
        result = skill.execute(dict(use["input"]))
        tool_results.append(
            {
                "toolResult": {
                    "toolUseId": use["toolUseId"],
                    "content": [{"json": result}],
                }
            }
        )

    messages.append({"role": "user", "content": tool_results})
    response = client.converse(
        modelId=model_id,
        system=[{"text": bundle["instructions"]}],
        messages=messages,
        toolConfig={"tools": [tool], "toolChoice": {"auto": {}}},
    )

for block in response["output"]["message"]["content"]:
    if "text" in block:
        print(block["text"])
```

Runnable copy: [`examples/bedrock_business_diagnostic.py`](../../examples/bedrock_business_diagnostic.py).

## Environment

| Variable | Purpose |
| :--- | :--- |
| `AWS_REGION` | Bedrock region (for example `us-east-1`) |
| `BEDROCK_MODEL_ID` | Model ID (for example `anthropic.claude-3-5-sonnet-20241022-v2:0`) |
| Skill `env_vars` | Separate from AWS — see the skill catalog page |

## Multi-skill hosts

```python
from skill_assay import SkillContext

ctx = SkillContext(categories=["compliance"], mode="brief")
tools = ctx.tools("bedrock")
```

## Related

- [Enterprise cloud overview](enterprise_cloud.md)
- [Agent loops](agent_loops.md)
- [Install extras](install_extras.md)
- [Usage guide index](README.md)
- [AWS Bedrock tool use docs](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use.html)

from __future__ import annotations

from pathlib import Path

from citationclaw.skills.base import SkillContext, SkillResult
from citationclaw.core.exporter import ResultExporter


class ExportSkill:
    name = "phase3_export"

    async def run(self, ctx: SkillContext, **kwargs) -> SkillResult:
        input_file = Path(kwargs["input_file"])
        excel_output = Path(kwargs["excel_output"])
        json_output = Path(kwargs["json_output"])

        exporter = ResultExporter(log_callback=ctx.log)
        exporter.export(
            input_file=input_file,
            excel_output=excel_output,
            json_output=json_output,
        )
        return SkillResult(
            name=self.name,
            data={
                "excel_output": str(excel_output),
                "json_output": str(json_output),
            },
        )

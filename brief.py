"""Concise presentation of governing context and the selected task frontier."""
from dataclasses import dataclass

from .strategic_context import StrategicContext
from .task_selection import TaskSelection


@dataclass(frozen=True)
class GhostBrief:
    context: StrategicContext
    selection: TaskSelection


def build_brief(context: StrategicContext, selection: TaskSelection) -> GhostBrief:
    return GhostBrief(context, selection)


def render_brief(brief: GhostBrief) -> str:
    selection = brief.selection
    lines = ['GHOST BRIEF', '', 'WHY', brief.context.why, '', 'DIRECTION',
             brief.context.direction, '', 'PRIMARY ORDER',
             selection.primary.title if selection.primary else 'None']
    if selection.ancestors:
        lines.extend(('', 'UNDER'))
        lines.extend(task.title for task in selection.ancestors)
    if selection.next:
        lines.extend(('', 'NEXT'))
        lines.extend(f'- {task.title}' for task in selection.next)
    if selection.primary is None:
        lines.extend(('', 'No actionable work.'))
    return '\n'.join(lines) + '\n'

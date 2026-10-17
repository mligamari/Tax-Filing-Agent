"""Collected problems found while reading, validating, writing or checking a file."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Issue:
    level: str      # "error" stops the file from being written; "warning" does not
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper()}: {self.where}: {self.message}"


@dataclass
class Issues:
    items: list[Issue] = field(default_factory=list)

    def error(self, where: str, message: str) -> None:
        self.items.append(Issue("error", where, message))

    def warning(self, where: str, message: str) -> None:
        self.items.append(Issue("warning", where, message))

    def extend(self, other: "Issues") -> None:
        self.items.extend(other.items)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.items if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.items if i.level == "warning"]

    def __bool__(self) -> bool:
        return bool(self.items)

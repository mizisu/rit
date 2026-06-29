"""PR data models matching GitHub GraphQL/REST response structures."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Generic, Literal, TypeVar, cast

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from rit.core.datetime_utils import datetime_min_utc, datetime_sort_key

__all__ = (
    "CommentThread",
    "FileViewedState",
    "LoadingState",
    "NodeList",
    "PR",
    "PRComment",
    "PRFile",
    "PRIssueComment",
    "PRLabel",
    "PRReview",
    "PRState",
    "PRTeam",
    "PRUser",
    "PendingReviewComment",
    "ReviewRequest",
    "ReviewState",
    "ReviewThread",
    "ReviewThreadInfo",
    "group_comments_into_threads",
)


T = TypeVar("T")
_LIST_TYPE = list


_FILE_STATUS_ICONS: dict[str, str] = {
    "added": "+",
    "removed": "-",
    "modified": "M",
    "renamed": "R",
    "copied": "C",
}


def _dict_value(value: object, key: str) -> object | None:
    if not isinstance(value, Mapping):
        return None
    return getattr(value, "get")(key)


class NodeList(BaseModel, Generic[T]):
    """GraphQL Connection's { nodes: [...] } wrapper."""

    nodes: list[T] = Field(default_factory=list)

    @classmethod
    def from_nodes(cls, nodes: Iterable[T]) -> "NodeList[T]":
        """Return a connection wrapper from any iterable of nodes."""
        if isinstance(nodes, _LIST_TYPE):
            node_count = len(nodes)
            if node_count == 0 or (node_count == 1 and isinstance(nodes[0], BaseModel)):
                return cls.model_construct(nodes=nodes)
            if all(isinstance(node, BaseModel) for node in nodes):
                return cls.model_construct(nodes=nodes)
        return cls(nodes=list(nodes))


class LoadingState(Enum):
    IDLE = "idle"
    LOADING = "loading"
    LOADED = "loaded"
    ERROR = "error"


class PRState(Enum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class ReviewState(Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    COMMENTED = "COMMENTED"
    DISMISSED = "DISMISSED"


class FileViewedState(Enum):
    UNVIEWED = "UNVIEWED"
    VIEWED = "VIEWED"
    DISMISSED = "DISMISSED"


@dataclass
class ReviewThreadInfo:
    """Store cache entry for a review thread keyed by root comment."""

    thread_id: str
    is_resolved: bool
    path: str
    line: int | None
    root_comment_id: int


class PRUser(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    login: str = ""
    avatar_url: str = Field(
        default="", validation_alias=AliasChoices("avatarUrl", "avatar_url")
    )


class PRTeam(BaseModel):
    name: str = ""
    slug: str = ""


class PRLabel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = ""
    color: str = ""
    description: str = ""


class PRIssueComment(BaseModel):
    """General comment on the PR (not on a specific line)."""

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(default=0, validation_alias=AliasChoices("databaseId", "id"))
    body: str = ""
    user: PRUser | None = Field(
        default=None, validation_alias=AliasChoices("author", "user")
    )
    created_at: datetime = Field(
        default_factory=datetime_min_utc,
        validation_alias=AliasChoices("createdAt", "created_at"),
    )
    updated_at: datetime = Field(
        default_factory=datetime_min_utc,
        validation_alias=AliasChoices("updatedAt", "updated_at"),
    )
    html_url: str = Field(
        default="", validation_alias=AliasChoices("htmlUrl", "html_url")
    )


class PRComment(BaseModel):
    """Review comment on a code line or file."""

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(default=0, validation_alias=AliasChoices("databaseId", "id"))
    body: str = ""
    user: PRUser | None = Field(
        default=None, validation_alias=AliasChoices("author", "user")
    )
    path: str = ""
    line: int | None = None
    original_line: int | None = Field(
        default=None, validation_alias=AliasChoices("originalLine", "original_line")
    )
    start_line: int | None = Field(
        default=None, validation_alias=AliasChoices("startLine", "start_line")
    )
    original_start_line: int | None = Field(
        default=None,
        validation_alias=AliasChoices("originalStartLine", "original_start_line"),
    )
    side: str = ""
    start_side: str = Field(
        default="", validation_alias=AliasChoices("startSide", "start_side")
    )
    created_at: datetime = Field(
        default_factory=datetime_min_utc,
        validation_alias=AliasChoices("createdAt", "created_at"),
    )
    updated_at: datetime = Field(
        default_factory=datetime_min_utc,
        validation_alias=AliasChoices("updatedAt", "updated_at"),
    )
    in_reply_to_id: int | None = Field(
        default=None, validation_alias=AliasChoices("replyTo", "in_reply_to_id")
    )
    diff_hunk: str = Field(
        default="", validation_alias=AliasChoices("diffHunk", "diff_hunk")
    )
    position: int | None = None
    original_position: int | None = Field(
        default=None,
        validation_alias=AliasChoices("originalPosition", "original_position"),
    )
    node_id: str = Field(default="", validation_alias=AliasChoices("nodeId", "node_id"))
    subject_type: str = Field(
        default="line", validation_alias=AliasChoices("subjectType", "subject_type")
    )
    pull_request_review_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices("pullRequestReview", "pull_request_review_id"),
    )

    @property
    def anchor_side(self) -> Literal["old", "new", "auto"]:
        if self.side == "LEFT":
            return "old"
        if self.side == "RIGHT":
            return "new"
        if self.original_line is not None and self.line is None:
            return "old"
        if self.line is not None:
            return "new"
        return "auto"

    @property
    def anchor_line(self) -> int | None:
        if self.side == "LEFT":
            return self.original_line if self.original_line is not None else self.line
        if self.side == "RIGHT":
            return self.line if self.line is not None else self.original_line
        if self.original_line is not None and self.line is None:
            return self.original_line
        return self.line if self.line is not None else self.original_line

    @field_validator("in_reply_to_id", mode="before")
    @classmethod
    def parse_reply_to(cls, v: object) -> object:
        if isinstance(v, dict):
            return _dict_value(v, "databaseId")
        return v

    @field_validator("pull_request_review_id", mode="before")
    @classmethod
    def parse_review_id(cls, v: object) -> object:
        if isinstance(v, dict):
            return _dict_value(v, "databaseId")
        return v


class PendingReviewComment(BaseModel):
    """Locally staged inline review comment awaiting review submission."""

    body: str = ""
    path: str = ""
    line: int = 0
    side: Literal["LEFT", "RIGHT"] = "RIGHT"
    start_line: int | None = None
    start_side: Literal["LEFT", "RIGHT"] | None = None
    is_diff_line: bool = True
    review_comment_id: int = 0

    @property
    def anchor_side(self) -> Literal["old", "new"]:
        return "old" if self.side == "LEFT" else "new"

    @property
    def anchor_line(self) -> int:
        return self.line


class ReviewRequest(BaseModel):
    """GraphQL reviewRequests node (handles both User and Team)."""

    model_config = ConfigDict(populate_by_name=True)

    requested_reviewer: PRUser | PRTeam | None = Field(
        default=None,
        validation_alias=AliasChoices("requestedReviewer", "requested_reviewer"),
    )

    @field_validator("requested_reviewer", mode="before")
    @classmethod
    def parse_requested_reviewer(cls, v: object) -> PRUser | PRTeam | None:
        if isinstance(v, (PRUser, PRTeam)) or v is None:
            return v
        if isinstance(v, dict):
            if _dict_value(v, "login"):
                return PRUser.model_validate(v)
            if _dict_value(v, "slug") or _dict_value(v, "name"):
                return PRTeam.model_validate(v)
        return None

    @property
    def display_name(self) -> str:
        if isinstance(self.requested_reviewer, PRUser):
            return self.requested_reviewer.login
        elif isinstance(self.requested_reviewer, PRTeam):
            return self.requested_reviewer.name or self.requested_reviewer.slug
        return ""

    @property
    def avatar_url(self) -> str:
        if isinstance(self.requested_reviewer, PRUser):
            return self.requested_reviewer.avatar_url
        return ""


class ReviewThread(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = ""  # GraphQL node ID for mutations
    is_resolved: bool = Field(
        default=False, validation_alias=AliasChoices("isResolved", "is_resolved")
    )
    path: str = ""
    line: int | None = None
    original_line: int | None = Field(
        default=None, validation_alias=AliasChoices("originalLine", "original_line")
    )
    start_line: int | None = Field(
        default=None, validation_alias=AliasChoices("startLine", "start_line")
    )
    original_start_line: int | None = Field(
        default=None,
        validation_alias=AliasChoices("originalStartLine", "original_start_line"),
    )
    diff_side: str = Field(
        default="", validation_alias=AliasChoices("diffSide", "diff_side")
    )
    start_diff_side: str | None = Field(
        default=None, validation_alias=AliasChoices("startDiffSide", "start_diff_side")
    )
    subject_type: str = Field(
        default="LINE", validation_alias=AliasChoices("subjectType", "subject_type")
    )

    comments_connection: NodeList[PRComment] = Field(
        default_factory=lambda: NodeList[PRComment](),
        validation_alias=AliasChoices("comments", "comments_connection"),
        exclude=True,
    )

    @property
    def comments(self) -> list[PRComment]:
        return self.comments_connection.nodes

    @property
    def root_comment(self) -> PRComment | None:
        return self.comments[0] if self.comments else None

    @property
    def root_comment_id(self) -> int:
        return self.comments[0].id if self.comments else 0

    @property
    def anchor_side(self) -> Literal["old", "new", "auto"]:
        if self.diff_side == "LEFT":
            return "old"
        if self.diff_side == "RIGHT":
            return "new"
        root = self.root_comment
        if root is not None and root.anchor_side != "auto":
            return root.anchor_side
        if self.original_line is not None and self.line is None:
            return "old"
        if self.line is not None:
            return "new"
        return "auto"

    @property
    def anchor_line(self) -> int | None:
        if self.diff_side == "LEFT":
            return self.original_line if self.original_line is not None else self.line
        if self.diff_side == "RIGHT":
            return self.line if self.line is not None else self.original_line
        root = self.root_comment
        if root is not None:
            root_side = root.anchor_side
            if root_side == "old":
                return (
                    self.original_line if self.original_line is not None else self.line
                )
            if root_side == "new":
                return self.line if self.line is not None else self.original_line
        if self.original_line is not None and self.line is None:
            return self.original_line
        return self.line if self.line is not None else self.original_line


class PRReview(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(default=0, validation_alias=AliasChoices("databaseId", "id"))
    node_id: str = Field(default="", validation_alias=AliasChoices("nodeId", "node_id"))
    user: PRUser | None = Field(
        default=None, validation_alias=AliasChoices("author", "user")
    )
    body: str = ""
    state: ReviewState = ReviewState.PENDING
    created_at: datetime = Field(
        default_factory=datetime_min_utc,
        validation_alias=AliasChoices("createdAt", "created_at"),
    )
    submitted_at: datetime | None = Field(
        default=None, validation_alias=AliasChoices("submittedAt", "submitted_at")
    )

    @field_validator("state", mode="before")
    @classmethod
    def parse_state(cls, v: object) -> object:
        if isinstance(v, str):
            try:
                return ReviewState(v)
            except ValueError:
                return ReviewState.PENDING
        return v


class PRFile(BaseModel):
    """A file changed in the PR (REST API response with patch data)."""

    model_config = ConfigDict(populate_by_name=True)

    filename: str = ""
    status: str = "modified"  # "added", "removed", "modified", "renamed", "copied"
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    patch: str = ""  # Unified diff patch - only available via REST API
    previous_filename: str | None = Field(
        default=None,
        validation_alias=AliasChoices("previousFilename", "previous_filename"),
    )
    sha: str = ""
    blob_url: str = Field(
        default="", validation_alias=AliasChoices("blobUrl", "blob_url")
    )
    raw_url: str = Field(default="", validation_alias=AliasChoices("rawUrl", "raw_url"))
    contents_url: str = Field(
        default="", validation_alias=AliasChoices("contentsUrl", "contents_url")
    )

    comments: list[PRComment] = Field(default_factory=list, exclude=True)
    viewer_viewed_state: FileViewedState = Field(
        default=FileViewedState.UNVIEWED, exclude=True
    )

    @property
    def display_name(self) -> str:
        if self.previous_filename:
            return f"{self.previous_filename} -> {self.filename}"
        return self.filename

    @property
    def status_icon(self) -> str:
        return _FILE_STATUS_ICONS.get(self.status, "?")


class PR(BaseModel):
    """Pull Request data (GraphQL response)."""

    model_config = ConfigDict(populate_by_name=True)

    node_id: str = Field(default="", validation_alias=AliasChoices("id", "node_id"))
    number: int = 0
    title: str = ""
    body: str = ""
    state: str = "OPEN"  # GraphQL returns OPEN/CLOSED/MERGED
    is_draft: bool = Field(
        default=False, validation_alias=AliasChoices("isDraft", "is_draft")
    )

    user: PRUser | None = Field(
        default=None, validation_alias=AliasChoices("author", "user")
    )

    created_at: datetime = Field(
        default_factory=datetime_min_utc,
        validation_alias=AliasChoices("createdAt", "created_at"),
    )
    updated_at: datetime = Field(
        default_factory=datetime_min_utc,
        validation_alias=AliasChoices("updatedAt", "updated_at"),
    )
    merged_at: datetime | None = Field(
        default=None, validation_alias=AliasChoices("mergedAt", "merged_at")
    )
    closed_at: datetime | None = Field(
        default=None, validation_alias=AliasChoices("closedAt", "closed_at")
    )

    head_ref: str = Field(
        default="", validation_alias=AliasChoices("headRefName", "head_ref")
    )
    base_ref: str = Field(
        default="", validation_alias=AliasChoices("baseRefName", "base_ref")
    )
    head_sha: str = Field(
        default="", validation_alias=AliasChoices("headRefOid", "head_sha")
    )
    base_sha: str = Field(
        default="", validation_alias=AliasChoices("baseRefOid", "base_sha")
    )

    additions: int = 0
    deletions: int = 0
    changed_files: int = Field(
        default=0, validation_alias=AliasChoices("changedFiles", "changed_files")
    )
    commits: int = 0
    comments_count: int = Field(
        default=0, validation_alias=AliasChoices("commentsCount", "comments_count")
    )
    review_comments_count: int = Field(
        default=0,
        validation_alias=AliasChoices("reviewCommentsCount", "review_comments_count"),
    )

    assignees_connection: NodeList[PRUser] = Field(
        default_factory=lambda: NodeList[PRUser](),
        validation_alias=AliasChoices("assignees", "assignees_connection"),
        exclude=True,
    )
    labels_connection: NodeList[PRLabel] = Field(
        default_factory=lambda: NodeList[PRLabel](),
        validation_alias=AliasChoices("labels", "labels_connection"),
        exclude=True,
    )
    review_requests_connection: NodeList[ReviewRequest] = Field(
        default_factory=lambda: NodeList[ReviewRequest](),
        validation_alias=AliasChoices("reviewRequests", "review_requests_connection"),
        exclude=True,
    )
    reviews_connection: NodeList[PRReview] = Field(
        default_factory=lambda: NodeList[PRReview](),
        validation_alias=AliasChoices("reviews", "reviews_connection"),
        exclude=True,
    )
    review_threads_connection: NodeList[ReviewThread] = Field(
        default_factory=lambda: NodeList[ReviewThread](),
        validation_alias=AliasChoices("reviewThreads", "review_threads_connection"),
        exclude=True,
    )
    issue_comments_connection: NodeList[PRIssueComment] = Field(
        default_factory=lambda: NodeList[PRIssueComment](),
        validation_alias=AliasChoices("comments", "issue_comments_connection"),
        exclude=True,
    )

    html_url: str = Field(
        default="", validation_alias=AliasChoices("htmlUrl", "html_url")
    )
    diff_url: str = Field(
        default="", validation_alias=AliasChoices("diffUrl", "diff_url")
    )
    patch_url: str = Field(
        default="", validation_alias=AliasChoices("patchUrl", "patch_url")
    )

    draft: bool = False
    mergeable: bool | None = None
    merged: bool = False

    @property
    def assignees(self) -> list[PRUser]:
        return self.assignees_connection.nodes

    @property
    def labels(self) -> list[PRLabel]:
        return self.labels_connection.nodes

    @property
    def requested_reviewers(self) -> list[ReviewRequest]:
        return self.review_requests_connection.nodes

    @property
    def reviews(self) -> list[PRReview]:
        return self.reviews_connection.nodes

    @property
    def review_threads(self) -> list[ReviewThread]:
        return self.review_threads_connection.nodes

    @property
    def issue_comments(self) -> list[PRIssueComment]:
        return self.issue_comments_connection.nodes

    @property
    def pr_state(self) -> PRState:
        if self.merged_at or self.merged or self.state == "MERGED":
            return PRState.MERGED
        elif self.state == "CLOSED" or self.closed_at:
            return PRState.CLOSED
        return PRState.OPEN

    @property
    def state_display(self) -> Literal["Open", "Merged", "Closed", "Draft"]:
        pr_state = self.pr_state
        if pr_state == PRState.MERGED:
            return "Merged"
        elif pr_state == PRState.CLOSED:
            return "Closed"
        elif self.is_draft or self.draft:
            return "Draft"
        return "Open"

    @property
    def state_color(self) -> str:
        pr_state = self.pr_state
        if pr_state == PRState.MERGED:
            return "purple"
        elif pr_state == PRState.CLOSED:
            return "red"
        elif self.is_draft or self.draft:
            return "gray"
        return "green"


class CommentThread(BaseModel):
    """Computed model grouping PRComments into a thread (not from API directly)."""

    model_config = ConfigDict(populate_by_name=True)

    root_comment: PRComment
    replies: list[PRComment] = Field(default_factory=list)
    is_resolved: bool = False
    thread_id: str = ""  # GraphQL node ID for resolve/unresolve mutations

    @field_validator("replies")
    @classmethod
    def sort_replies(cls, replies: list[PRComment]) -> list[PRComment]:
        if len(replies) < 2:
            return replies
        reply_iter = iter(replies)
        previous_key = datetime_sort_key(next(reply_iter).created_at)
        for reply in reply_iter:
            key = datetime_sort_key(reply.created_at)
            if key < previous_key:
                return sorted(replies, key=lambda c: datetime_sort_key(c.created_at))
            previous_key = key
        return replies

    @property
    def file_path(self) -> str:
        return self.root_comment.path

    @property
    def line(self) -> int | None:
        return self.root_comment.line or self.root_comment.original_line

    @property
    def all_comments(self) -> list[PRComment]:
        return [self.root_comment, *self.replies]

    @property
    def created_at(self) -> datetime:
        return self.root_comment.created_at


def group_comments_into_threads(comments: Iterable[PRComment]) -> list[CommentThread]:
    if isinstance(comments, Sequence):
        comment_sequence = cast("Sequence[PRComment]", comments)
        comment_count = len(comment_sequence)
        if comment_count == 0:
            return []
        if comment_count == 1:
            return [CommentThread(root_comment=comment_sequence[0])]

    comment_map: dict[int, PRComment] = {comment.id: comment for comment in comments}
    root_comments: list[PRComment] = []
    replies_map: dict[int, list[PRComment]] = {}

    for comment in comment_map.values():
        if comment.in_reply_to_id is None:
            root_comments.append(comment)
        elif comment.in_reply_to_id in comment_map:
            root_id = comment.in_reply_to_id
            while comment_map.get(root_id) and comment_map[root_id].in_reply_to_id:
                parent_id = comment_map[root_id].in_reply_to_id
                if parent_id in comment_map:
                    root_id = parent_id
                else:
                    break

            if root_id not in replies_map:
                replies_map[root_id] = []
            replies_map[root_id].append(comment)
        else:
            root_comments.append(comment)

    threads: list[CommentThread] = []
    for root in root_comments:
        thread = CommentThread(
            root_comment=root,
            replies=replies_map.get(root.id, []),
        )
        threads.append(thread)

    if len(threads) > 1:
        threads.sort(key=lambda t: datetime_sort_key(t.created_at))

    return threads

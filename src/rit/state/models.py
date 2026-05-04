"""PR data models matching GitHub GraphQL/REST response structures."""

from datetime import datetime
from enum import Enum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


T = TypeVar("T")


class NodeList(BaseModel, Generic[T]):
    """GraphQL Connection's { nodes: [...] } wrapper."""

    nodes: list[T] = Field(default_factory=list)


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


class PRUser(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    login: str = ""
    avatar_url: str = Field(default="", alias="avatarUrl")


class PRTeam(BaseModel):
    name: str = ""


class PRLabel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = ""
    color: str = ""
    description: str = ""


class PRIssueComment(BaseModel):
    """General comment on the PR (not on a specific line)."""

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(default=0, alias="databaseId")
    body: str = ""
    user: PRUser | None = Field(default=None, alias="author")
    created_at: datetime = Field(
        default_factory=lambda: datetime.min, alias="createdAt"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.min, alias="updatedAt"
    )
    html_url: str = Field(default="", alias="htmlUrl")


class PRComment(BaseModel):
    """Review comment on a specific line of code."""

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(default=0, alias="databaseId")
    body: str = ""
    user: PRUser | None = Field(default=None, alias="author")
    path: str = ""
    line: int | None = None
    original_line: int | None = Field(default=None, alias="originalLine")
    side: str = ""
    created_at: datetime = Field(
        default_factory=lambda: datetime.min, alias="createdAt"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.min, alias="updatedAt"
    )
    in_reply_to_id: int | None = Field(default=None, alias="replyTo")
    diff_hunk: str = Field(default="", alias="diffHunk")
    node_id: str = Field(default="", alias="nodeId")
    pull_request_review_id: int | None = Field(default=None, alias="pullRequestReview")

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
        if self.anchor_side == "old":
            return self.original_line if self.original_line is not None else self.line
        if self.anchor_side == "new":
            return self.line if self.line is not None else self.original_line
        return self.line if self.line is not None else self.original_line

    @field_validator("in_reply_to_id", mode="before")
    @classmethod
    def parse_reply_to(cls, v: Any) -> int | None:
        if isinstance(v, dict):
            return v.get("databaseId")
        return v

    @field_validator("pull_request_review_id", mode="before")
    @classmethod
    def parse_review_id(cls, v: Any) -> int | None:
        if isinstance(v, dict):
            return v.get("databaseId")
        return v


class PendingReviewComment(BaseModel):
    """Locally staged inline review comment awaiting review submission."""

    body: str = ""
    path: str = ""
    line: int = 0
    side: Literal["LEFT", "RIGHT"] = "RIGHT"

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
        default=None, alias="requestedReviewer"
    )

    @property
    def display_name(self) -> str:
        if isinstance(self.requested_reviewer, PRUser):
            return self.requested_reviewer.login
        elif isinstance(self.requested_reviewer, PRTeam):
            return self.requested_reviewer.name
        return ""

    @property
    def avatar_url(self) -> str:
        if isinstance(self.requested_reviewer, PRUser):
            return self.requested_reviewer.avatar_url
        return ""


class ReviewThread(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = ""  # GraphQL node ID for mutations
    is_resolved: bool = Field(default=False, alias="isResolved")
    path: str = ""
    line: int | None = None
    original_line: int | None = Field(default=None, alias="originalLine")
    start_line: int | None = Field(default=None, alias="startLine")
    original_start_line: int | None = Field(default=None, alias="originalStartLine")
    diff_side: str = Field(default="", alias="diffSide")
    start_diff_side: str | None = Field(default=None, alias="startDiffSide")
    subject_type: str = Field(default="LINE", alias="subjectType")

    comments_connection: NodeList[PRComment] = Field(
        default_factory=NodeList, alias="comments", exclude=True
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
        if self.anchor_side == "old":
            return self.original_line if self.original_line is not None else self.line
        if self.anchor_side == "new":
            return self.line if self.line is not None else self.original_line
        return self.line if self.line is not None else self.original_line


class PRReview(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(default=0, alias="databaseId")
    user: PRUser | None = Field(default=None, alias="author")
    body: str = ""
    state: ReviewState = ReviewState.PENDING
    submitted_at: datetime | None = Field(default=None, alias="submittedAt")

    @field_validator("state", mode="before")
    @classmethod
    def parse_state(cls, v: Any) -> ReviewState:
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
    previous_filename: str | None = Field(default=None, alias="previousFilename")
    sha: str = ""
    blob_url: str = Field(default="", alias="blobUrl")
    raw_url: str = Field(default="", alias="rawUrl")
    contents_url: str = Field(default="", alias="contentsUrl")

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
        icons = {
            "added": "+",
            "removed": "-",
            "modified": "M",
            "renamed": "R",
            "copied": "C",
        }
        return icons.get(self.status, "?")


class PR(BaseModel):
    """Pull Request data (GraphQL response)."""

    model_config = ConfigDict(populate_by_name=True)

    node_id: str = Field(default="", alias="id")
    number: int = 0
    title: str = ""
    body: str = ""
    state: str = "OPEN"  # GraphQL returns OPEN/CLOSED/MERGED
    is_draft: bool = Field(default=False, alias="isDraft")

    user: PRUser | None = Field(default=None, alias="author")

    created_at: datetime = Field(
        default_factory=lambda: datetime.min, alias="createdAt"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.min, alias="updatedAt"
    )
    merged_at: datetime | None = Field(default=None, alias="mergedAt")
    closed_at: datetime | None = Field(default=None, alias="closedAt")

    head_ref: str = Field(default="", alias="headRefName")
    base_ref: str = Field(default="", alias="baseRefName")
    head_sha: str = Field(default="", alias="headRefOid")
    base_sha: str = Field(default="", alias="baseRefOid")

    additions: int = 0
    deletions: int = 0
    changed_files: int = Field(default=0, alias="changedFiles")
    commits: int = 0
    comments_count: int = Field(default=0, alias="commentsCount")
    review_comments_count: int = Field(default=0, alias="reviewCommentsCount")

    assignees_connection: NodeList[PRUser] = Field(
        default_factory=NodeList, alias="assignees", exclude=True
    )
    labels_connection: NodeList[PRLabel] = Field(
        default_factory=NodeList, alias="labels", exclude=True
    )
    review_requests_connection: NodeList[ReviewRequest] = Field(
        default_factory=NodeList, alias="reviewRequests", exclude=True
    )
    reviews_connection: NodeList[PRReview] = Field(
        default_factory=NodeList, alias="reviews", exclude=True
    )
    review_threads_connection: NodeList[ReviewThread] = Field(
        default_factory=NodeList, alias="reviewThreads", exclude=True
    )
    issue_comments_connection: NodeList[PRIssueComment] = Field(
        default_factory=NodeList, alias="comments", exclude=True
    )

    html_url: str = Field(default="", alias="htmlUrl")
    diff_url: str = Field(default="", alias="diffUrl")
    patch_url: str = Field(default="", alias="patchUrl")

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
    def state_display(self) -> str:
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

    @property
    def file_path(self) -> str:
        return self.root_comment.path

    @property
    def line(self) -> int | None:
        return self.root_comment.line or self.root_comment.original_line

    @property
    def all_comments(self) -> list[PRComment]:
        return [self.root_comment] + sorted(self.replies, key=lambda c: c.created_at)

    @property
    def created_at(self) -> datetime:
        return self.root_comment.created_at


def group_comments_into_threads(comments: list[PRComment]) -> list[CommentThread]:
    comment_map: dict[int, PRComment] = {c.id: c for c in comments}
    root_comments: list[PRComment] = []
    replies_map: dict[int, list[PRComment]] = {}

    for comment in comments:
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

    threads.sort(key=lambda t: t.created_at)

    return threads

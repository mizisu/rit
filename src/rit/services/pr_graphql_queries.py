from enum import Enum

__all__ = (
    "PullRequestGraphQLView",
    "pull_request_graphql_request",
    "pull_request_query",
)


class PullRequestGraphQLView(Enum):
    """Named PR GraphQL payload shapes used by GitHubService."""

    ALL = "all"
    SUMMARY = "summary"
    DISCUSSION = "discussion"
    FAST_DISCUSSION = "fast_discussion"
    TIMELINE = "timeline"
    NODE_ID = "node_id"


_PR_TIMELINE_GRAPHQL_FRAGMENT = """
fragment TimelineEvents on PullRequest {
  timelineItems(first: 100, after: $endCursor, itemTypes: [
    PULL_REQUEST_COMMIT, HEAD_REF_FORCE_PUSHED_EVENT,
    READY_FOR_REVIEW_EVENT, CONVERT_TO_DRAFT_EVENT,
    MERGED_EVENT, CLOSED_EVENT, REOPENED_EVENT,
    REVIEW_REQUESTED_EVENT, REVIEW_REQUEST_REMOVED_EVENT,
    ISSUE_COMMENT, PULL_REQUEST_REVIEW
  ]) {
    pageInfo { hasNextPage endCursor }
    nodes {
      __typename
      ... on Node { id }
      ... on IssueComment { databaseId createdAt }
      ... on PullRequestReview { databaseId createdAt }
      ... on PullRequestCommit {
        commit {
          oid
          messageHeadline
          committedDate
          author { name user { login } }
        }
      }
      ... on HeadRefForcePushedEvent {
        createdAt
        actor { login }
        beforeCommit { oid }
        afterCommit { oid }
      }
      ... on ReadyForReviewEvent { createdAt actor { login } }
      ... on ConvertToDraftEvent { createdAt actor { login } }
      ... on MergedEvent {
        createdAt
        actor { login }
        mergeRefName
        commit { oid }
      }
      ... on ClosedEvent { createdAt actor { login } }
      ... on ReopenedEvent { createdAt actor { login } }
      ... on ReviewRequestedEvent {
        createdAt
        actor { login }
        requestedReviewer {
          ... on User { login }
          ... on Team { name }
        }
      }
      ... on ReviewRequestRemovedEvent {
        createdAt
        actor { login }
        requestedReviewer {
          ... on User { login }
          ... on Team { name }
        }
      }
    }
  }
}
"""

_PR_TIMELINE_GRAPHQL_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      ...TimelineEvents
    }
  }
}
"""


_PR_SUMMARY_GRAPHQL_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      id
      number
      title
      body
      state
      isDraft
      additions
      deletions
      changedFiles
      createdAt
      updatedAt
      mergedAt
      closedAt
      author {
        login
        avatarUrl
      }
      baseRefName
      headRefName
      baseRefOid
      headRefOid
      assignees(first: 20) {
        nodes {
          login
          avatarUrl
        }
      }
      labels(first: 50) {
        nodes {
          name
          color
          description
        }
      }
      reviewRequests(first: 20) {
        nodes {
          requestedReviewer {
            ... on User {
              login
              avatarUrl
            }
            ... on Team {
              name
              slug
            }
          }
        }
      }
    }
  }
}
"""


_PR_DISCUSSION_GRAPHQL_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      ...TimelineEvents
      body
      reviews(first: 100) {
        nodes {
          nodeId: id
          databaseId
          author {
            login
            avatarUrl
          }
          state
          body
          createdAt
          submittedAt
        }
      }
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          path
          line
          originalLine
          startLine
          originalStartLine
          diffSide
          startDiffSide
          subjectType
          comments(first: 100) {
            nodes {
              nodeId: id
              databaseId
              author {
                login
                avatarUrl
              }
              body
              createdAt
              publishedAt
              updatedAt
              diffHunk
              path
              line
              originalLine
              startLine
              originalStartLine
              replyTo {
                databaseId
              }
              pullRequestReview {
                databaseId
              }
              commit {
                oid
              }
              originalCommit {
                oid
              }
              outdated
              subjectType
            }
          }
        }
      }
      comments(first: 100) {
        nodes {
          databaseId
          author {
            login
            avatarUrl
          }
          body
          createdAt
          updatedAt
        }
      }
    }
  }
}
"""


_PR_FAST_DISCUSSION_GRAPHQL_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      ...TimelineEvents
      body
      reviews(first: 100) {
        nodes {
          nodeId: id
          databaseId
          author {
            login
            avatarUrl
          }
          state
          body
          createdAt
          submittedAt
        }
      }
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          path
          line
          originalLine
          startLine
          originalStartLine
          diffSide
          startDiffSide
          subjectType
          comments(first: 100) {
            nodes {
              nodeId: id
              databaseId
              author {
                login
                avatarUrl
              }
              body
              createdAt
              publishedAt
              updatedAt
              diffHunk
              path
              line
              originalLine
              startLine
              originalStartLine
              replyTo {
                databaseId
              }
              pullRequestReview {
                databaseId
              }
              commit {
                oid
              }
              originalCommit {
                oid
              }
              outdated
              subjectType
            }
          }
        }
      }
      comments(first: 100) {
        nodes {
          databaseId
          author {
            login
            avatarUrl
          }
          body
          createdAt
          updatedAt
        }
      }
    }
  }
}
"""


_PR_GRAPHQL_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      ...TimelineEvents
      id
      number
      title
      body
      state
      isDraft
      additions
      deletions
      changedFiles
      createdAt
      updatedAt
      mergedAt
      closedAt
      
      author {
        login
        avatarUrl
      }
      
      baseRefName
      headRefName
      baseRefOid
      headRefOid
      
      assignees(first: 20) {
        nodes {
          login
          avatarUrl
        }
      }
      
      labels(first: 50) {
        nodes {
          name
          color
          description
        }
      }
      
      reviewRequests(first: 20) {
        nodes {
          requestedReviewer {
            ... on User {
              login
              avatarUrl
            }
            ... on Team {
              name
              slug
            }
          }
        }
      }
      
      reviews(first: 100) {
        nodes {
          nodeId: id
          databaseId
          author {
            login
            avatarUrl
          }
          state
          body
          createdAt
          submittedAt
        }
      }
      
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          path
          line
          originalLine
          startLine
          originalStartLine
          diffSide
          startDiffSide
          subjectType
          comments(first: 100) {
            nodes {
              nodeId: id
              databaseId
              author {
                login
                avatarUrl
              }
              body
              createdAt
              publishedAt
              updatedAt
              diffHunk
              path
              line
              originalLine
              startLine
              originalStartLine
              replyTo {
                databaseId
              }
              pullRequestReview {
                databaseId
              }
              commit {
                oid
              }
              originalCommit {
                oid
              }
              outdated
              subjectType
            }
          }
        }
      }
      
      comments(first: 100) {
        nodes {
          databaseId
          author {
            login
            avatarUrl
          }
          body
          createdAt
          updatedAt
        }
      }
    }
  }
}
"""


_PR_NODE_ID_GRAPHQL_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      id
    }
  }
}
"""


_PULL_REQUEST_QUERIES = {
    PullRequestGraphQLView.ALL: _PR_GRAPHQL_QUERY,
    PullRequestGraphQLView.SUMMARY: _PR_SUMMARY_GRAPHQL_QUERY,
    PullRequestGraphQLView.DISCUSSION: _PR_DISCUSSION_GRAPHQL_QUERY,
    PullRequestGraphQLView.FAST_DISCUSSION: _PR_FAST_DISCUSSION_GRAPHQL_QUERY,
    PullRequestGraphQLView.TIMELINE: _PR_TIMELINE_GRAPHQL_QUERY,
    PullRequestGraphQLView.NODE_ID: _PR_NODE_ID_GRAPHQL_QUERY,
}


def pull_request_query(view: PullRequestGraphQLView) -> str:
    """Return the GraphQL document for a named PR payload shape."""
    query = _PULL_REQUEST_QUERIES[view]
    if view in {
        PullRequestGraphQLView.ALL,
        PullRequestGraphQLView.DISCUSSION,
        PullRequestGraphQLView.FAST_DISCUSSION,
        PullRequestGraphQLView.TIMELINE,
    }:
        return query + _PR_TIMELINE_GRAPHQL_FRAGMENT
    return query


def pull_request_graphql_request(
    *,
    view: PullRequestGraphQLView,
    owner: str,
    repo: str,
    pr_number: int,
) -> tuple[str, ...]:
    """Build gh args for a named PR GraphQL query."""
    return (
        "api",
        "graphql",
        "-f",
        f"query={pull_request_query(view)}",
        "-F",
        f"owner={owner}",
        "-F",
        f"repo={repo}",
        "-F",
        f"number={pr_number}",
    )

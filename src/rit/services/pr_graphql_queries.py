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
    NODE_ID = "node_id"


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
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
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
              databaseId
              author {
                login
                avatarUrl
              }
              body
              createdAt
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
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
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
              databaseId
              author {
                login
                avatarUrl
              }
              body
              createdAt
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
              databaseId
              author {
                login
                avatarUrl
              }
              body
              createdAt
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
    PullRequestGraphQLView.NODE_ID: _PR_NODE_ID_GRAPHQL_QUERY,
}


def pull_request_query(view: PullRequestGraphQLView) -> str:
    """Return the GraphQL document for a named PR payload shape."""
    return _PULL_REQUEST_QUERIES[view]


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

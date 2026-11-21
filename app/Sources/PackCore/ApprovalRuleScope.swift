import Foundation

/// Concrete key for a persistent approval rule.
///
/// Empty action or target values are wildcards in the Hub rule engine, so this
/// initializer rejects either missing dimension. Keep the action-tag pattern
/// aligned with ACTION_TAG_RE in hub/hub.py.
public struct ApprovalRuleScope: Equatable {
    public let action: String
    public let target: String

    public init?(taskTitle: String, assignee: String?) {
        let target = assignee?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !target.isEmpty,
              let range = taskTitle.range(
                of: #"\[[A-Za-z0-9_-]+\]"#,
                options: .regularExpression
              ) else {
            return nil
        }

        self.action = String(taskTitle[range])
        self.target = target
    }
}

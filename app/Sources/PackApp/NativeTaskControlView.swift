import SwiftUI
import PackCore

/// Reusable native task presentation. Only a trusted worker launcher may
/// start the controller. This panel does not start a browser on appearance.
struct NativeTaskControlView: View {
    @ObservedObject var controller: NativeTaskExecutionController

    var body: some View {
        VStack(alignment:.leading,spacing:DesignTokens.boardGap) {
            Text("原生 Agent 执行控制").font(DesignTokens.shellTitle)
            Text(verbatim:"任务 #\(controller.taskID) · \(controller.destination.provider)")
            Text(verbatim:controller.destination.accountRef)
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText).textSelection(.enabled)
            if let pending = controller.pending {
                approvalPanel(pending)
            } else {
                Text(phaseLabel).foregroundStyle(DesignTokens.subText)
                if controller.phase == .running || controller.phase == .stopping {
                    ProgressView().controlSize(.small)
                }
            }
            if let report = controller.report {
                Divider()
                Text(receiptLabel(report.receipt)).font(DesignTokens.fontTitle)
                Text(report.childExitConfirmed ? "子进程正常退出；不代表浏览器或远端模型已停止。" :
                     "子进程退出未确认；请核对账号租约与任务状态。")
                    .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.amber)
            }
            HStack {
                Spacer()
                Button("停止本次执行") { controller.stop() }
                    .disabled(controller.phase != .running)
            }
            Text("停止是本地取消请求，不保证远端模型停止。不会自动重试或删除租约。")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
        }
        .font(DesignTokens.fontUI)
        .onDisappear { controller.stop() }
    }

    private func approvalPanel(_ pending: NativeTaskExecutionController.Pending) -> some View {
        VStack(alignment:.leading,spacing:DesignTokens.detailSpacing) {
            switch pending.approval {
            case .context(_,let context):
                Text("第一步：确认任务上下文").font(DesignTokens.fontTitle)
                Text("协作角色：\(context.role.rawValue) · 步骤：\(context.stepKey)")
                ScrollView {
                    VStack(alignment:.leading,spacing:DesignTokens.detailSpacing) {
                        ForEach(Array(context.items.enumerated()),id:\.offset) { _,item in
                            Text(verbatim:"\(item.kind) #\(item.sourceID)").font(DesignTokens.fontSmall)
                            Text(verbatim:item.text).textSelection(.enabled)
                            Divider()
                        }
                    }.frame(maxWidth:.infinity,alignment:.leading)
                }.frame(minHeight:100,maxHeight:260)
            case .send(_,let context,let origin,let conversation,let text,let hash,_):
                Text("第二步：确认完整发送文本").font(DesignTokens.fontTitle)
                Text(verbatim:origin).textSelection(.enabled)
                Text(verbatim:"会话：\(conversation) · \(context.role.rawValue)")
                Text(verbatim:"SHA-256：\(hash)").font(DesignTokens.fontSmall).textSelection(.enabled)
                ScrollView {
                    Text(verbatim:text).font(.system(size:12,design:.monospaced))
                        .textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading)
                }.frame(minHeight:100,maxHeight:260)
            }
            Text("本次授权到期：\(pending.expires.formatted(date:.omitted,time:.standard))")
                .font(DesignTokens.fontSmall).foregroundStyle(DesignTokens.subText)
            HStack {
                Button("拒绝") { controller.decide(pending.id,allow:false) }
                Spacer()
                Button("仅授权本次") { controller.decide(pending.id,allow:true) }
                    .buttonStyle(.borderedProminent)
            }
        }
    }

    private var phaseLabel: String {
        switch controller.phase {
        case .idle: return "尚未启动"
        case .running: return "执行中 · 等待下一步或 Hub 回执"
        case .stopping: return "已请求停止 · 等待退出确认"
        case .finished: return "本地执行流程结束"
        }
    }

    private func receiptLabel(_ value:NativeTaskBrokerReceipt?) -> String {
        switch value {
        case .some(.review): return "Hub 已确认成果，等待审核"
        case .some(.blocked): return "Hub 已确认任务阻塞"
        case .some(.notStarted): return "尚未认领任务"
        case .some(.unavailable): return "本次认领未获准"
        case .some(.claimed): return "已认领 · 无终态回执"
        case .some(.claimUnknown): return "认领结果未确认"
        case .some(.executionUnconfirmed): return "已授权发送 · 执行结果未确认"
        case .some(.providerUnknown): return "Provider 执行结果未确认"
        case .some(.stopUnknown): return "停止结果未确认"
        case .some(.deliveryUnknown): return "成果交付结果未确认"
        case .none: return "无可信 Hub 回执"
        }
    }
}

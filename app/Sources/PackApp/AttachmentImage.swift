import AppKit
import ImageIO
import UniformTypeIdentifiers

enum AttachmentLimits {
    static let maxCount = 4
    static let imageCacheCount = 64
    static let maxBytes = 10 * 1024 * 1024
    static let maxDimension = 2048
    static let jpegQuality = 0.85
}

struct PreparedAttachment {
    let data: Data
    let mime: String
    let filename: String
}

enum AttachmentPreparationError: LocalizedError {
    case unsupported, unreadable, oversized
    var errorDescription: String? {
        switch self {
        case .unsupported: return "仅支持 PNG、JPEG、WebP 和 GIF 图片。"
        case .unreadable: return "无法读取图片，请重新选择。"
        case .oversized: return "图片压缩后仍超过 10 MB，请选择较小的图片。"
        }
    }
}

enum AttachmentImage {
    static func prepare(_ url: URL) throws -> PreparedAttachment {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let data = try Data(contentsOf: url, options: .mappedIfSafe)
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let type = CGImageSourceGetType(source) as String? else { throw AttachmentPreparationError.unreadable }
        let mime: String
        switch type {
        case UTType.png.identifier: mime = "image/png"
        case UTType.jpeg.identifier: mime = "image/jpeg"
        case UTType.gif.identifier: mime = "image/gif"
        case UTType.webP.identifier: mime = "image/webp"
        default: throw AttachmentPreparationError.unsupported
        }
        guard let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = props[kCGImagePropertyPixelWidth] as? Int,
              let height = props[kCGImagePropertyPixelHeight] as? Int else { throw AttachmentPreparationError.unreadable }
        if data.count <= AttachmentLimits.maxBytes && max(width, height) <= AttachmentLimits.maxDimension {
            return PreparedAttachment(data: data, mime: mime, filename: url.lastPathComponent)
        }
        let options: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true,
                                         kCGImageSourceCreateThumbnailWithTransform: true,
                                         kCGImageSourceThumbnailMaxPixelSize: AttachmentLimits.maxDimension]
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else { throw AttachmentPreparationError.unreadable }
        let output = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(output, UTType.jpeg.identifier as CFString, 1, nil) else { throw AttachmentPreparationError.unreadable }
        CGImageDestinationAddImage(destination, image, [kCGImageDestinationLossyCompressionQuality: AttachmentLimits.jpegQuality] as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { throw AttachmentPreparationError.unreadable }
        let bytes = output as Data
        guard bytes.count <= AttachmentLimits.maxBytes else { throw AttachmentPreparationError.oversized }
        return PreparedAttachment(data: bytes, mime: "image/jpeg", filename: url.deletingPathExtension().lastPathComponent + ".jpg")
    }
}

import { createHttpClient } from "./client";
import type { PdfAnnotation, PdfAnnotationCreate, PdfAnnotationUpdate } from "./types";

// PDF 批注接口：超时放宽至 20s（同步到后端时可能涉及网络）
const http = createHttpClient({
  timeout: 20000,
  defaultMessage: "PDF 批注操作失败",
});

/** 列出某篇论文的所有批注 */
export async function listPdfAnnotations(paperId: string): Promise<PdfAnnotation[]> {
  const { data } = await http.get<PdfAnnotation[]>(`/papers/${paperId}/pdf-annotations`);
  return data;
}

/** 新建批注（前端高亮后可选同步到后端） */
export async function createPdfAnnotation(payload: PdfAnnotationCreate): Promise<PdfAnnotation> {
  const { data } = await http.post<PdfAnnotation>(
    `/papers/${payload.paperId}/pdf-annotations`,
    payload,
  );
  return data;
}

/** 更新批注（颜色 / 批注文字） */
export async function updatePdfAnnotation(
  annotationId: string,
  payload: PdfAnnotationUpdate,
): Promise<PdfAnnotation> {
  const { data } = await http.patch<PdfAnnotation>(`/pdf-annotations/${annotationId}`, payload);
  return data;
}

/** 删除批注 */
export async function deletePdfAnnotation(annotationId: string): Promise<void> {
  await http.delete(`/pdf-annotations/${annotationId}`);
}

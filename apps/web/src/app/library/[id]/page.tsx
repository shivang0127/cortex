import { DocumentView } from "@/components/DocumentView";

export default async function DocumentPage({ params }: PageProps<"/library/[id]">) {
  const { id } = await params;
  return <DocumentView documentId={id} />;
}

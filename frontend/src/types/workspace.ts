export interface WorkspaceRun {
  id: string;
  kind:
    | "text_to_image"
    | "fusion"
    | "multi_view"
    | "multi_view_split"
    | "sketch_to_realistic"
    | "product_refine"
    | "gemstone_design"
    | "upscale"
    | "grayscale_relief";
  title: string;
  model: string;
  provider: string;
  status: string;
  imageUrl: string | null;
  sourceImageUrl?: string | null;
  sourceImages?: string[] | null;
  primaryImageIndex?: number | null;
  splitItems?: Array<{
    view: string;
    imageUrl: string | null;
    storageUrl?: string | null;
    width: number;
    height: number;
  }> | null;
  createdAt: string;
  prompt: string;
}

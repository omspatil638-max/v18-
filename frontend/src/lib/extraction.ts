import { isProcessing } from "./types";
import type { ContractStatus, ExtractionStatus } from "./types";

interface ExtractionState {
  status: ContractStatus;
  extraction_status: ExtractionStatus;
  extraction_error: string | null;
}

/**
 * Honest explanation for an empty list of extracted items.
 * `noun` is plural ("parties"); `noneFound` is the sentence used when extraction completed and found nothing.
 */
export function emptyStateText(c: ExtractionState, noun: string, noneFound: string): string {
  if (isProcessing(c.status)) {
    return `Still processing. Extracted ${noun} will appear here when processing finishes.`;
  }
  const reason = c.extraction_error ? ` ${c.extraction_error}` : "";
  switch (c.extraction_status) {
    case "unavailable":
      return `No ${noun} could be extracted because AI extraction is unavailable for this contract.${reason}`;
    case "unsupported":
      return `No ${noun} were extracted because this document has no readable text layer.${reason}`;
    case "legacy":
      return `This contract was processed by an older version and its ${noun} were not verified. Re-run extraction to check them.`;
    case "partial":
      return `${noneFound} Extraction was only partial, so some ${noun} may be missing.${reason}`;
    case "complete":
      return noneFound;
    default:
      return `No ${noun} have been extracted.`;
  }
}

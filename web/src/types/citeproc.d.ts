declare module "citeproc" {
  export interface CitationItem {
    id: string;
    prefix?: string;
    suffix?: string;
    locator?: string;
    label?: string;
    suppressAuthor?: boolean;
    authorOnly?: boolean;
  }

  export interface CitationCluster {
    citationID?: string;
    citationItems: CitationItem[];
    properties: {
      noteIndex: number;
    };
  }

  export interface Engine {
    updateItems(ids: string[]): void;
    makeBibliography(): [unknown, string[]] | undefined;
    processCitationCluster(
      cluster: CitationCluster,
      citationsPre: unknown[],
      citationsPost: unknown[],
    ): [string, [string, string, string][]];
  }

  export interface Sys {
    retrieveItem(id: string): Record<string, unknown>;
    retrieveLocale(lang: string): string | null;
  }

  interface CSLConstructor {
    new (sys: Sys, styleXml: string, lang: string, forceLang?: string): Engine;
  }

  const CSL: {
    Engine: CSLConstructor;
  };

  export default CSL;
}

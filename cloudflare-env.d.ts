declare namespace Cloudflare {
  interface Env {
    BAZE_API_KEY?: string;
    DB?: D1Database;
    BUCKET?: R2Bucket;
  }
}

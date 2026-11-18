declare namespace Cloudflare {
  interface Env {
    BAZE_API_KEY?: string;
    BACKEND_API_URL?: string;
    BACKEND_SHARED_TOKEN?: string;
    DB?: D1Database;
    BUCKET?: R2Bucket;
  }
}

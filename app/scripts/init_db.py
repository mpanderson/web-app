import os
import sys
from sqlalchemy import text
from app.db import engine
from app.models import Base

def main():
    try:
        Base.metadata.create_all(bind=engine)
        with engine.begin() as con:
            try:
                con.execute(text("CREATE INDEX IF NOT EXISTS idx_close_date ON opportunities (close_date)"))
            except Exception:
                pass
        print("DB initialized.")
    except Exception as e:
        error_msg = str(e)
        
        # Check if this is a deployment environment trying to connect to development database
        if "helium" in error_msg or "could not translate host name" in error_msg:
            print("\n" + "="*80)
            print("❌ DATABASE CONNECTION FAILED")
            print("="*80)
            print("\n⚠️  Your deployment is trying to connect to the development database ('helium')")
            print("   which is not available in Cloud Run deployments.\n")
            print("📋 TO FIX THIS:")
            print("   1. Open the 'Deployments' pane in Replit")
            print("   2. Click on your deployment")
            print("   3. Click 'Add Database' and select PostgreSQL")
            print("   4. Redeploy your application")
            print("\n   The DATABASE_URL will be automatically configured with your production database.\n")
            print("="*80 + "\n")
            
            # In production deployment, exit with error
            if os.getenv("REPL_DEPLOYMENT") or os.getenv("REPLIT_DEPLOYMENT"):
                sys.exit(1)
        else:
            print(f"❌ Database initialization failed: {error_msg}")
            if os.getenv("REPL_DEPLOYMENT") or os.getenv("REPLIT_DEPLOYMENT"):
                sys.exit(1)
        
        raise

if __name__ == "__main__":
    main()

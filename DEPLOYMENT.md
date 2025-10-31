# RFA Matcher - Deployment Guide

## 🚀 Deploying to Production (Cloud Run)

Your app is ready for deployment! Follow these steps to publish it:

### ✅ Prerequisites (Already Complete)

- ✅ Image size optimized (~15 MB)
- ✅ Import paths fixed for deployment
- ✅ psycopg2-binary installed for PostgreSQL
- ✅ .dockerignore configured
- ✅ Environment variable handling set up

### 📋 Required: Add PostgreSQL Database

**IMPORTANT:** The deployment will fail without this step!

Your app currently uses the Replit development database (`helium`), which is **not available** in Cloud Run deployments. You must add a production PostgreSQL database:

1. **Open the Deployments pane** in Replit (left sidebar)
2. **Click on your deployment** (or create one if you haven't yet)
3. **Click "Add Database"** button
4. **Select PostgreSQL** (Replit managed PostgreSQL on Neon)
5. **Wait for provisioning** (takes ~1-2 minutes)
6. The `DATABASE_URL` environment variable will be **automatically set** with the production database connection string
7. **Redeploy your app** to apply the changes

### 🔑 Environment Variables

Make sure these secrets are configured in your deployment:

- ✅ `OPENAI_API_KEY` - For embeddings and LLM reranking
- ✅ `GRANTS_GOV_API_KEY` - For Grants.gov API access
- ✅ `SAM_GOV_API_KEY` - For SAM.gov SBIR/STTR opportunities
- 🔄 `DATABASE_URL` - **Automatically set when you add PostgreSQL database**

### 🎯 Deployment Type

Use **Autoscale** deployment for this app:
- Automatically scales with traffic
- Idles to zero when not in use (cost-effective)
- Perfect for web APIs with variable workload

### ⚙️ What Happens After Deployment

1. App starts and connects to production PostgreSQL database
2. Creates database schema (opportunities table)
3. Starts background scheduler for twice-daily ingestion (12 PM and 8 PM EST)
4. First ingestion runs automatically
5. Generates vector embeddings for semantic search
6. API becomes available at your deployment URL

### 🔍 Verifying Deployment

After deployment, test these endpoints:

```bash
# Check API is responding
curl https://your-deployment-url.replit.app/

# Check opportunities loaded
curl https://your-deployment-url.replit.app/opportunities/stats

# Check scheduler status
curl https://your-deployment-url.replit.app/scheduler/status
```

### ⚠️ Common Issues

**Error: "could not translate host name 'helium'"**
- **Cause:** PostgreSQL database not added to deployment
- **Fix:** Follow the "Add PostgreSQL Database" steps above

**Error: "Database connection failed"**
- **Cause:** DATABASE_URL not configured or invalid
- **Fix:** Check that PostgreSQL database was successfully added in Deployments pane

**Error: "Application startup failed"**
- **Cause:** Usually database connection issue
- **Fix:** Check deployment logs and verify DATABASE_URL is set

### 📊 Monitoring

- **Logs:** View in Deployments pane → Your deployment → Logs
- **Scheduler Status:** `/scheduler/status` endpoint
- **Database Stats:** `/opportunities/stats` endpoint
- **Health Check:** Root endpoint `/`

### 💰 Cost Considerations

**Deployment:**
- Autoscale deployment scales to zero when idle
- Compute units only charged when handling requests

**Database:**
- Replit PostgreSQL: Separate pricing (check Replit pricing page)
- Persistent storage included

**API Costs (External):**
- OpenAI embeddings: ~$0.02 per 1,000 opportunities
- Twice-daily ingestion with 1,500+ opportunities ≈ $0.03/day

### 🎉 Next Steps

After successful deployment:

1. ✅ Verify opportunities are being ingested
2. ✅ Test the matching API with a researcher profile
3. ✅ Monitor scheduler logs for successful runs
4. ✅ Set up custom domain (optional)
5. ✅ Configure production monitoring/alerting

---

## 🆘 Need Help?

If deployment fails:
1. Check deployment logs in Replit Deployments pane
2. Verify PostgreSQL database was added successfully
3. Ensure all environment variables are set
4. Check that DATABASE_URL doesn't contain "helium"

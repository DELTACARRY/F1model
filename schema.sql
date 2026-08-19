BEGIN TRANSACTION;
CREATE TABLE aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        model_id INTEGER NOT NULL REFERENCES models(id) ON DELETE CASCADE,
        alias TEXT NOT NULL,
        alias_type TEXT DEFAULT 'keyword',
        UNIQUE(model_id, alias)
    );
CREATE TABLE market_listings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        model_id INTEGER REFERENCES models(id) ON DELETE SET NULL,
        source TEXT NOT NULL,
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        seller TEXT,
        condition TEXT,
        buying_option TEXT,
        price REAL,
        currency TEXT,
        shipping REAL,
        shipping_currency TEXT,
        total_cny REAL,
        image TEXT,
        price_confidence TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        UNIQUE(source, url)
    );
CREATE TABLE models (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        brand TEXT NOT NULL,
        driver TEXT,
        team TEXT,
        season INTEGER,
        car_model TEXT,
        chassis_code TEXT,
        race TEXT,
        edition TEXT,
        scale TEXT,
        product_type TEXT DEFAULT '赛车模型',
        sku TEXT,
        limited_qty INTEGER,
        official_image TEXT,
        official_url TEXT,
        notes TEXT,
        verification_status TEXT DEFAULT '待补充',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
CREATE TABLE price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        listing_id INTEGER NOT NULL REFERENCES market_listings(id) ON DELETE CASCADE,
        observed_at TEXT NOT NULL,
        price REAL,
        currency TEXT,
        shipping REAL,
        total_cny REAL
    );
CREATE TABLE watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        model_id INTEGER REFERENCES models(id) ON DELETE CASCADE,
        query TEXT,
        target_cny REAL,
        sources TEXT,
        enabled INTEGER DEFAULT 1,
        created_at TEXT NOT NULL
    );
CREATE INDEX idx_models_brand ON models(brand);
CREATE INDEX idx_models_driver ON models(driver);
CREATE INDEX idx_models_car ON models(car_model);
CREATE INDEX idx_models_sku ON models(sku);
CREATE INDEX idx_listing_model ON market_listings(model_id);
CREATE INDEX idx_listing_seen ON market_listings(last_seen);
CREATE INDEX idx_price_history_listing ON price_history(listing_id, observed_at);
COMMIT;

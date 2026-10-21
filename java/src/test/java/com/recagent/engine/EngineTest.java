package com.recagent.engine;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.recagent.catalog.Catalog;
import com.recagent.config.AppSettings;
import com.recagent.model.Domain.DroppedItem;
import com.recagent.model.Domain.EngineOutput;
import com.recagent.model.Domain.UserSnapshot;
import com.recagent.support.TestSupport;
import java.util.Map;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;

class EngineTest {
  @Test
  void hardFilterDropsClosedBlockedDietary() {
    Catalog catalog = TestSupport.catalog();
    RecommendationEngine eng = new RecommendationEngine(catalog, new AppSettings());
    UserSnapshot snap = catalog.getUser("u_dietary");
    EngineOutput out = eng.run(snap, TestSupport.scene("s1"));
    Map<String, String> dropped =
        out.dropped.stream().collect(Collectors.toMap(DroppedItem::merchantId, DroppedItem::reason));
    assertEquals("closed", dropped.get("m_closed_1"));
    assertEquals("blocked", dropped.get("m_blocked_demo"));
    assertEquals("dietary", dropped.get("m_spicy_1"));
    assertFalse(out.candidates.stream().anyMatch(c -> "m_spicy_1".equals(c.merchantId)));
    assertFalse(out.candidates.stream().anyMatch(c -> "m_blocked_demo".equals(c.merchantId)));
    assertFalse(out.candidates.stream().anyMatch(c -> "m_closed_1".equals(c.merchantId)));
  }

  @Test
  void noodleRegularKeepsNoodle() {
    Catalog catalog = TestSupport.catalog();
    RecommendationEngine eng = new RecommendationEngine(catalog, new AppSettings());
    UserSnapshot snap = catalog.getUser("u_noodle_regular");
    EngineOutput out = eng.run(snap, TestSupport.scene("s_noodle"));
    assertTrue(!out.candidates.isEmpty());
    assertTrue(
        out.candidates.stream().limit(3).anyMatch(c -> "面食".equals(c.category)));
  }
}

package com.likyly.model;

import java.util.List;

/**
 * One page of the catalog.
 *
 * @param items 
 * @param total the whole catalog's size (X-Total-Count), null if the API did not report it
 * @param limit the page size you asked for (null = the API's default)
 * @param offset 
 */
public record ItemList(
    List<Item> items,
    Long total,
    Integer limit,
    int offset) {
}

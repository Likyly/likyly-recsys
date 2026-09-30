package com.likyly.model;

import java.util.List;

/**
 * One page of users.
 *
 * @param users 
 * @param total 
 * @param limit 
 * @param offset 
 */
public record UserList(
    List<User> users,
    Long total,
    Integer limit,
    int offset) {
}
